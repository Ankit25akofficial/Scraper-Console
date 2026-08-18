import os
import time
import logging
import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, Query, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse
from pydantic_settings import BaseSettings

from app.models import (
    ScrapeResponse,
    HealthResponse,
    StatusResponse,
    ProxyStatus,
    JobModel
)
from app.proxy_manager import ProxyManager
from app.fingerprint import FingerprintGenerator
from app.scraper import GitHubJobsScraper, StackOverflowScraper, IndeedScraper

# Define Settings Class
class Settings(BaseSettings):
    PROXY_LIST: Optional[str] = None
    USER_AGENT_LIST: Optional[str] = None
    MAX_RETRIES: int = 3
    DELAY_RANGE: str = "3,5"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

# Load config
settings = Settings()

# Parse delay range
try:
    min_d, max_d = map(float, settings.DELAY_RANGE.split(","))
    delay_range = (min_d, max_d)
except Exception:
    delay_range = (3.0, 5.0)

# Initialize application
app = FastAPI(title="Acdyon Scraper API", version="1.0.0")

# Setup logging buffer for dashboard feed
class ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.logs = []

    def emit(self, record):
        log_entry = f"[{datetime.now().strftime('%H:%M:%S')}] {record.levelname}: {record.getMessage()}"
        self.logs.append(log_entry)
        if len(self.logs) > 50:  # Keep last 50 lines
            self.logs.pop(0)

# Configure logging
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
log_handler = ListHandler()
log_handler.setFormatter(logging.Formatter('%(message)s'))
root_logger.addHandler(log_handler)

# Extra stdout handler to see terminal logs
stdout_handler = logging.StreamHandler()
stdout_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
root_logger.addHandler(stdout_handler)

logger = logging.getLogger(__name__)

# Initialize components
proxy_manager = ProxyManager(proxy_list_str=settings.PROXY_LIST)
fingerprint_gen = FingerprintGenerator(
    custom_user_agents=[u.strip() for u in settings.USER_AGENT_LIST.split(",")] if settings.USER_AGENT_LIST else None
)

# In-Memory Statistics
start_time = datetime.utcnow()
metrics = {
    "total_requests": 0,
    "successful_requests": 0,
    "failed_requests": 0,
    "last_scrape_time": None,
    "rate_limit_hits": 0,
    "recent_jobs": []
}

async def run_proxy_checks():
    """Background task to run proxy health check."""
    logger.info("Starting background proxy health check...")
    await proxy_manager.check_all_proxies()
    logger.info("Proxy health check complete.")

@app.on_event("startup")
async def startup_event():
    # Run initial proxy health check in the background
    asyncio.create_task(run_proxy_checks())

@app.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse()

@app.get("/status", response_model=StatusResponse)
async def get_status():
    uptime = (datetime.utcnow() - start_time).total_seconds()
    success_rate = 100.0
    if metrics["total_requests"] > 0:
        success_rate = (metrics["successful_requests"] / metrics["total_requests"]) * 100.0

    proxy_statuses = [
        ProxyStatus(
            address=p["address"],
            is_alive=p["is_alive"],
            fail_count=p["fail_count"],
            latency_ms=p["latency_ms"]
        ) for p in proxy_manager.get_all_statuses()
    ]

    return StatusResponse(
        uptime_seconds=uptime,
        total_requests=metrics["total_requests"],
        successful_requests=metrics["successful_requests"],
        failed_requests=metrics["failed_requests"],
        success_rate=round(success_rate, 2),
        active_proxies_count=len([p for p in proxy_statuses if p.is_alive]),
        proxies=proxy_statuses,
        last_scrape_time=metrics["last_scrape_time"],
        rate_limit_hits=metrics["rate_limit_hits"]
    )

@app.get("/scrape", response_model=ScrapeResponse)
async def scrape_endpoint(
    source: str = Query(..., description="Source website to scrape (github, stackoverflow, indeed)"),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    valid_sources = ["github", "stackoverflow", "indeed"]
    if source.lower() not in valid_sources:
        raise HTTPException(status_code=400, detail=f"Invalid source. Must be one of: {', '.join(valid_sources)}")

    metrics["total_requests"] += 1
    logger.info(f"Received scrape request for source: '{source}'")

    try:
        if source.lower() == "github":
            scraper = GitHubJobsScraper(
                proxy_manager=proxy_manager,
                fingerprint_gen=fingerprint_gen,
                max_retries=settings.MAX_RETRIES,
                delay_range=delay_range
            )
        elif source.lower() == "stackoverflow":
            scraper = StackOverflowScraper(
                proxy_manager=proxy_manager,
                fingerprint_gen=fingerprint_gen,
                max_retries=settings.MAX_RETRIES,
                delay_range=delay_range
            )
        else:
            scraper = IndeedScraper(
                proxy_manager=proxy_manager,
                fingerprint_gen=fingerprint_gen,
                max_retries=settings.MAX_RETRIES,
                delay_range=delay_range
            )

        # Run scrape operation
        response: ScrapeResponse = await scraper.scrape()
        
        # Update metrics
        if response.jobs:
            metrics["successful_requests"] += 1
            metrics["last_scrape_time"] = datetime.utcnow()
            metrics["recent_jobs"] = response.jobs[:15]  # Keep last 15 scraped jobs
        else:
            metrics["failed_requests"] += 1
            logger.error(f"Scraper returned empty list for source '{source}'. Marking as failed.")
            
        metrics["rate_limit_hits"] += scraper.rate_limit_hits

        # Trigger proxy check if failure rate rises
        background_tasks.add_task(run_proxy_checks)

        return response

    except Exception as e:
        metrics["failed_requests"] += 1
        logger.critical(f"Unhandled error while scraping '{source}': {str(e)}")
        # Trigger proxy check in background
        background_tasks.add_task(run_proxy_checks)
        raise HTTPException(status_code=500, detail=f"Scrape failed: {str(e)}")

@app.get("/logs")
async def get_system_logs():
    """Returns in-memory logs for the dashboard log viewer."""
    return {"logs": log_handler.logs}

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serves the status dashboard using vanilla CSS and JavaScript."""
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Acdyon Scraper System Status</title>
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=Plus+Jakarta+Sans:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
        <style>
            :root {
                --bg-primary: #05030b;
                --bg-secondary: #0d0a1b;
                --card-bg: rgba(22, 19, 44, 0.45);
                --card-hover: rgba(30, 26, 59, 0.7);
                --accent-purple: #c084fc;
                --accent-blue: #38bdf8;
                --accent-green: #4ade80;
                --accent-orange: #fb923c;
                --accent-red: #f87171;
                --text-primary: #f8fafc;
                --text-secondary: #94a3b8;
                --border-color: rgba(168, 85, 247, 0.15);
                --border-hover: rgba(56, 189, 248, 0.4);
                --font-sans: 'Plus Jakarta Sans', sans-serif;
                --font-display: 'Outfit', sans-serif;
                --font-mono: 'JetBrains Mono', monospace;
            }

            * {
                box-sizing: border-box;
                margin: 0;
                padding: 0;
            }

            body {
                background-color: var(--bg-primary);
                color: var(--text-primary);
                font-family: var(--font-sans);
                overflow-x: hidden;
                padding: 3rem 1.5rem;
                line-height: 1.6;
                position: relative;
                min-height: 100vh;
            }

            /* Premium Mesh Gradient Background Spots */
            .ambient-glow-1 {
                position: absolute;
                width: 600px;
                height: 600px;
                border-radius: 50%;
                background: radial-gradient(circle, rgba(168, 85, 247, 0.1) 0%, rgba(0, 0, 0, 0) 70%);
                top: -150px;
                right: -100px;
                z-index: -2;
                pointer-events: none;
                animation: float-slow 20s infinite alternate;
            }

            .ambient-glow-2 {
                position: absolute;
                width: 500px;
                height: 500px;
                border-radius: 50%;
                background: radial-gradient(circle, rgba(56, 189, 248, 0.08) 0%, rgba(0, 0, 0, 0) 70%);
                bottom: 100px;
                left: -150px;
                z-index: -2;
                pointer-events: none;
                animation: float-slow 25s infinite alternate-reverse;
            }

            @keyframes float-slow {
                0% { transform: translate(0, 0) scale(1); }
                100% { transform: translate(50px, 30px) scale(1.1); }
            }

            .container {
                max-width: 1200px;
                margin: 0 auto;
                position: relative;
            }

            /* Header styling */
            header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 3.5rem;
                padding-bottom: 1.75rem;
                border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            }

            .brand {
                display: flex;
                align-items: center;
                gap: 1rem;
            }

            .logo-icon {
                width: 3rem;
                height: 3rem;
                background: linear-gradient(135deg, #a855f7, #38bdf8);
                border-radius: 12px;
                display: flex;
                align-items: center;
                justify-content: center;
                font-family: var(--font-display);
                font-weight: 800;
                font-size: 1.5rem;
                color: white;
                box-shadow: 0 8px 24px rgba(168, 85, 247, 0.35);
                position: relative;
            }

            .logo-icon::after {
                content: '';
                position: absolute;
                inset: -2px;
                border-radius: 14px;
                background: linear-gradient(135deg, #c084fc, #7dd3fc);
                z-index: -1;
                opacity: 0.5;
            }

            .brand h1 {
                font-family: var(--font-display);
                font-size: 1.75rem;
                font-weight: 700;
                letter-spacing: -0.5px;
                background: linear-gradient(to right, #ffffff, #cbd5e1);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
            }

            .status-badge {
                display: flex;
                align-items: center;
                gap: 0.6rem;
                background: rgba(74, 222, 128, 0.06);
                border: 1px solid rgba(74, 222, 128, 0.2);
                padding: 0.5rem 1rem;
                border-radius: 100px;
                font-size: 0.85rem;
                font-weight: 600;
                color: var(--accent-green);
                box-shadow: 0 4px 12px rgba(74, 222, 128, 0.03);
            }

            .pulse-dot {
                width: 8px;
                height: 8px;
                background-color: var(--accent-green);
                border-radius: 50%;
                box-shadow: 0 0 10px var(--accent-green);
                animation: pulse 2s infinite;
            }

            @keyframes pulse {
                0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(74, 222, 128, 0.5); }
                70% { transform: scale(1); box-shadow: 0 0 0 8px rgba(74, 222, 128, 0); }
                100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(74, 222, 128, 0); }
            }

            /* Stats Grid Layout */
            .grid-stats {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
                gap: 1.5rem;
                margin-bottom: 3rem;
            }

            .card {
                background: var(--card-bg);
                backdrop-filter: blur(20px);
                -webkit-backdrop-filter: blur(20px);
                border: 1px solid var(--border-color);
                border-radius: 16px;
                padding: 1.75rem;
                box-shadow: 0 12px 40px 0 rgba(0, 0, 0, 0.35);
                position: relative;
                overflow: hidden;
                transition: transform 0.25s ease, border-color 0.25s ease, box-shadow 0.25s ease;
            }

            .card::before {
                content: '';
                position: absolute;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: linear-gradient(180deg, rgba(255, 255, 255, 0.02) 0%, rgba(255, 255, 255, 0) 100%);
                pointer-events: none;
            }

            .card:hover {
                transform: translateY(-4px);
                border-color: var(--border-hover);
                box-shadow: 0 16px 48px 0 rgba(168, 85, 247, 0.12);
            }

            .card-content-wrap {
                display: flex;
                justify-content: space-between;
                align-items: flex-start;
            }

            .card-info {
                display: flex;
                flex-direction: column;
                gap: 0.5rem;
            }

            .card-title {
                color: var(--text-secondary);
                font-family: var(--font-display);
                font-size: 0.85rem;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 1.25px;
            }

            .card-val {
                font-family: var(--font-display);
                font-size: 2.25rem;
                font-weight: 700;
                letter-spacing: -1px;
                color: #ffffff;
            }

            .val-green {
                color: var(--accent-green);
                text-shadow: 0 0 15px rgba(74, 222, 128, 0.15);
            }

            .val-blue {
                color: var(--accent-blue);
                text-shadow: 0 0 15px rgba(56, 189, 248, 0.15);
            }

            .card-icon {
                width: 2.5rem;
                height: 2.5rem;
                border-radius: 10px;
                background: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.05);
                display: flex;
                align-items: center;
                justify-content: center;
                color: var(--text-secondary);
            }

            .card:hover .card-icon {
                color: #ffffff;
                background: rgba(168, 85, 247, 0.1);
                border-color: rgba(168, 85, 247, 0.3);
            }

            /* Control & Terminal Section */
            .panel-control {
                display: grid;
                grid-template-columns: 1fr;
                gap: 2rem;
                margin-bottom: 3rem;
            }

            @media(min-width: 992px) {
                .panel-control {
                    grid-template-columns: 1.1fr 1.4fr;
                }
            }

            .action-box {
                display: flex;
                flex-direction: column;
                justify-content: space-between;
                gap: 1.5rem;
            }

            .control-title {
                font-family: var(--font-display);
                font-size: 1.25rem;
                font-weight: 600;
                letter-spacing: -0.25px;
            }

            .input-group {
                display: flex;
                flex-direction: column;
                gap: 0.6rem;
            }

            .input-label {
                font-size: 0.85rem;
                color: var(--text-secondary);
                font-weight: 500;
            }

            .select-wrapper {
                position: relative;
                width: 100%;
            }

            .select-style {
                width: 100%;
                background: rgba(10, 8, 22, 0.7);
                border: 1px solid var(--border-color);
                border-radius: 10px;
                padding: 0.85rem 2.5rem 0.85rem 1rem;
                color: white;
                font-family: var(--font-sans);
                font-size: 0.95rem;
                outline: none;
                cursor: pointer;
                appearance: none;
                -webkit-appearance: none;
                transition: border-color 0.25s ease, box-shadow 0.25s ease;
            }

            .select-style:focus {
                border-color: var(--accent-blue);
                box-shadow: 0 0 12px rgba(56, 189, 248, 0.2);
            }

            .select-wrapper::after {
                content: '';
                position: absolute;
                right: 1.2rem;
                top: 50%;
                transform: translateY(-50%);
                width: 0;
                height: 0;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 5px solid var(--text-secondary);
                pointer-events: none;
            }

            .btn-scrape {
                width: 100%;
                background: linear-gradient(135deg, #a855f7 0%, #38bdf8 100%);
                color: white;
                border: none;
                border-radius: 10px;
                padding: 1rem;
                font-size: 0.95rem;
                font-weight: 600;
                font-family: var(--font-display);
                cursor: pointer;
                box-shadow: 0 6px 20px rgba(168, 85, 247, 0.25);
                position: relative;
                overflow: hidden;
                transition: transform 0.2s, box-shadow 0.2s;
            }

            .btn-scrape::before {
                content: '';
                position: absolute;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: linear-gradient(to right, rgba(255,255,255,0.1), rgba(255,255,255,0));
                transform: translateX(-100%);
                transition: transform 0.6s ease;
            }

            .btn-scrape:hover::before {
                transform: translateX(100%);
            }

            .btn-scrape:hover {
                transform: translateY(-1px);
                box-shadow: 0 8px 24px rgba(56, 189, 248, 0.35);
            }

            .btn-scrape:active {
                transform: translateY(1px);
            }

            .btn-scrape:disabled {
                background: rgba(255, 255, 255, 0.05) !important;
                color: rgba(255, 255, 255, 0.2) !important;
                cursor: not-allowed;
                box-shadow: none !important;
                border: 1px solid rgba(255, 255, 255, 0.05);
            }

            /* Terminal mock styling */
            .terminal-window {
                display: flex;
                flex-direction: column;
                background: #040208;
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 14px;
                box-shadow: inset 0 2px 12px rgba(0, 0, 0, 0.9), 0 12px 40px rgba(0, 0, 0, 0.5);
                overflow: hidden;
            }

            .terminal-header {
                background: rgba(255, 255, 255, 0.02);
                border-bottom: 1px solid rgba(255, 255, 255, 0.05);
                padding: 0.75rem 1.25rem;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }

            .terminal-dots {
                display: flex;
                gap: 0.4rem;
            }

            .dot {
                width: 10px;
                height: 10px;
                border-radius: 50%;
            }

            .dot-red { background-color: var(--accent-red); opacity: 0.8; }
            .dot-yellow { background-color: var(--accent-orange); opacity: 0.8; }
            .dot-green { background-color: var(--accent-green); opacity: 0.8; }

            .terminal-title {
                font-family: var(--font-mono);
                font-size: 0.75rem;
                color: var(--text-secondary);
                letter-spacing: 0.5px;
            }

            .terminal-body {
                padding: 1.25rem;
                font-family: var(--font-mono);
                font-size: 0.825rem;
                height: 220px;
                overflow-y: auto;
                color: #cbd5e1;
                display: flex;
                flex-direction: column;
                gap: 0.35rem;
            }

            /* Terminal Line Colors */
            .log-line {
                white-space: pre-wrap;
            }
            .log-info { color: #94a3b8; }
            .log-warning { color: #fbbf24; }
            .log-error { color: #f87171; font-weight: 500; }
            .log-system { color: #38bdf8; font-weight: 500; }
            .log-success { color: #4ade80; }

            /* Proxy Pool Styling */
            .proxy-pool-title {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 1.25rem;
            }

            .proxy-grid {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
                gap: 1rem;
            }

            .proxy-card {
                background: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.04);
                padding: 1rem 1.25rem;
                border-radius: 12px;
                display: flex;
                justify-content: space-between;
                align-items: center;
                transition: border-color 0.25s, background-color 0.25s;
            }

            .proxy-card:hover {
                border-color: rgba(56, 189, 248, 0.25);
                background: rgba(56, 189, 248, 0.03);
            }

            .proxy-info {
                display: flex;
                flex-direction: column;
                gap: 0.25rem;
            }

            .proxy-ip {
                font-family: var(--font-mono);
                font-size: 0.85rem;
                color: #e2e8f0;
            }

            .proxy-latency {
                font-size: 0.75rem;
                color: var(--text-secondary);
                display: flex;
                align-items: center;
                gap: 0.25rem;
            }

            .proxy-badge {
                padding: 0.25rem 0.6rem;
                border-radius: 6px;
                font-size: 0.75rem;
                font-weight: 600;
                letter-spacing: 0.25px;
            }

            .badge-active {
                background: rgba(74, 222, 128, 0.08);
                border: 1px solid rgba(74, 222, 128, 0.2);
                color: var(--accent-green);
            }

            .badge-dead {
                background: rgba(248, 113, 113, 0.08);
                border: 1px solid rgba(248, 113, 113, 0.2);
                color: var(--accent-red);
            }

            /* Job Feed Styling */
            .jobs-container {
                margin-top: 4rem;
            }

            .section-title {
                font-family: var(--font-display);
                font-size: 1.5rem;
                font-weight: 700;
                margin-bottom: 1.5rem;
                display: flex;
                align-items: center;
                gap: 0.75rem;
                border-left: 4px solid var(--accent-purple);
                padding-left: 0.75rem;
            }

            .job-card {
                background: rgba(13, 10, 28, 0.4);
                backdrop-filter: blur(10px);
                border: 1px solid rgba(255, 255, 255, 0.03);
                border-left: 4px solid #a855f7;
                border-radius: 12px;
                padding: 1.5rem;
                margin-bottom: 1.25rem;
                display: flex;
                flex-direction: column;
                gap: 0.75rem;
                transition: transform 0.25s, border-color 0.25s, box-shadow 0.25s;
            }

            .job-card:hover {
                transform: translateX(4px);
                border-color: rgba(56, 189, 248, 0.25);
                box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
            }

            .job-header {
                display: flex;
                justify-content: space-between;
                align-items: flex-start;
                flex-wrap: wrap;
                gap: 0.75rem;
            }

            .job-title {
                font-family: var(--font-display);
                font-size: 1.2rem;
                font-weight: 600;
                color: #ffffff;
            }

            .job-company {
                color: var(--accent-blue);
                font-weight: 600;
                font-size: 0.95rem;
                margin-top: 0.15rem;
            }

            .job-location-badge {
                font-size: 0.8rem;
                background: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.06);
                padding: 0.3rem 0.7rem;
                border-radius: 100px;
                color: #e2e8f0;
                display: flex;
                align-items: center;
                gap: 0.35rem;
            }

            .job-desc {
                font-size: 0.925rem;
                color: #cbd5e1;
                line-height: 1.5;
            }

            .job-footer {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-top: 0.5rem;
                padding-top: 0.75rem;
                border-top: 1px solid rgba(255, 255, 255, 0.04);
            }

            .job-date {
                font-size: 0.8rem;
                color: var(--text-secondary);
                display: flex;
                align-items: center;
                gap: 0.35rem;
            }

            .btn-apply {
                background: rgba(56, 189, 248, 0.08);
                border: 1px solid rgba(56, 189, 248, 0.2);
                color: var(--accent-blue);
                padding: 0.45rem 1.1rem;
                border-radius: 8px;
                font-size: 0.85rem;
                font-weight: 600;
                text-decoration: none;
                transition: background-color 0.2s, color 0.2s, border-color 0.2s;
            }

            .btn-apply:hover {
                background: var(--accent-blue);
                color: var(--bg-primary);
                border-color: var(--accent-blue);
                box-shadow: 0 4px 12px rgba(56, 189, 248, 0.2);
            }

            .no-jobs {
                text-align: center;
                padding: 4rem 2rem;
                color: var(--text-secondary);
                background: rgba(13, 10, 28, 0.25);
                border: 1px dashed rgba(168, 85, 247, 0.2);
                border-radius: 16px;
                font-size: 0.95rem;
                display: flex;
                flex-direction: column;
                align-items: center;
                gap: 0.75rem;
            }

            .empty-state-icon {
                color: var(--accent-purple);
                opacity: 0.7;
            }

            /* Spinner and load structures */
            .loader {
                border: 2px solid rgba(255, 255, 255, 0.2);
                border-top: 2px solid #ffffff;
                border-radius: 50%;
                width: 16px;
                height: 16px;
                animation: spin 0.8s linear infinite;
                display: inline-block;
                vertical-align: middle;
                margin-right: 0.5rem;
            }

            @keyframes spin {
                0% { transform: rotate(0deg); }
                100% { transform: rotate(360deg); }
            }

            ::-webkit-scrollbar {
                width: 6px;
                height: 6px;
            }

            ::-webkit-scrollbar-track {
                background: rgba(0, 0, 0, 0.05);
            }

            ::-webkit-scrollbar-thumb {
                background: rgba(255, 255, 255, 0.1);
                border-radius: 10px;
            }

            ::-webkit-scrollbar-thumb:hover {
                background: rgba(255, 255, 255, 0.2);
            }
        </style>
    </head>
    <body>
        <div class="ambient-glow-1"></div>
        <div class="ambient-glow-2"></div>

        <div class="container">
            <header>
                <div class="brand">
                    <div class="logo-icon">A</div>
                    <div>
                        <h1>Acdyon Scraper Console</h1>
                        <p style="font-size: 0.8rem; color: var(--text-secondary); font-weight: 500;">Enterprise Job Aggregation Portal</p>
                    </div>
                </div>
                <div class="status-badge">
                    <div class="pulse-dot"></div>
                    <span>SYSTEM ONLINE</span>
                </div>
            </header>

            <!-- Statistics Grid -->
            <div class="grid-stats">
                <div class="card">
                    <div class="card-content-wrap">
                        <div class="card-info">
                            <div class="card-title">Scrape Requests</div>
                            <div class="card-val" id="stat-requests">0</div>
                        </div>
                        <div class="card-icon">
                            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path><polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline><line x1="12" y1="22.08" x2="12" y2="12"></line></svg>
                        </div>
                    </div>
                </div>
                <div class="card">
                    <div class="card-content-wrap">
                        <div class="card-info">
                            <div class="card-title">Success Rate</div>
                            <div class="card-val val-green" id="stat-rate">100%</div>
                        </div>
                        <div class="card-icon">
                            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path><path d="m9 11 2 2 4-4"></path></svg>
                        </div>
                    </div>
                </div>
                <div class="card">
                    <div class="card-content-wrap">
                        <div class="card-info">
                            <div class="card-title">Active Proxies</div>
                            <div class="card-val val-blue" id="stat-proxies">0</div>
                        </div>
                        <div class="card-icon">
                            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"></circle><path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32l1.41 1.41M2 12h2m16 0h2M6.34 17.66l-1.41 1.41m12.72-12.72l-1.41 1.41"></path></svg>
                        </div>
                    </div>
                </div>
                <div class="card">
                    <div class="card-content-wrap">
                        <div class="card-info">
                            <div class="card-title">Rate Limit Hits</div>
                            <div class="card-val" id="stat-ratelimits" style="color: var(--accent-orange);">0</div>
                        </div>
                        <div class="card-icon">
                            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect><path d="M7 11V7a5 5 0 0 1 9.9-1"></path></svg>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Controller Panel & Logs -->
            <div class="panel-control">
                <div class="card action-box">
                    <h2 class="control-title">Scraper Sequence Control</h2>
                    <div class="input-group">
                        <label class="input-label">Active Scraping Channel</label>
                        <div class="select-wrapper">
                            <select id="scrape-source" class="select-style">
                                <option value="github">GitHub Jobs API (w/ WeWorkRemotely RSS Fallback)</option>
                                <option value="stackoverflow">StackOverflow Jobs RSS (w/ Python.org RSS Fallback)</option>
                                <option value="indeed">Indeed.com (Conceptual Playwright Browser Scraper)</option>
                            </select>
                        </div>
                    </div>
                    <button class="btn-scrape" id="btn-scrape-trigger" onclick="triggerScrape()">
                        <span id="btn-text">Execute Scrape Sequence</span>
                    </button>
                </div>

                <div class="terminal-window">
                    <div class="terminal-header">
                        <div class="terminal-dots">
                            <div class="dot dot-red"></div>
                            <div class="dot dot-yellow"></div>
                            <div class="dot dot-green"></div>
                        </div>
                        <div class="terminal-title">system_monitor.sh</div>
                        <div style="width: 42px;"></div> <!-- visual spacing balance -->
                    </div>
                    <div class="terminal-body" id="log-terminal">
                        <div class="log-line log-info">[00:00:00] INFO: Log console initialized. Ready.</div>
                    </div>
                </div>
            </div>

            <!-- Proxy Pool Grid -->
            <div class="card" style="margin-bottom: 3rem;">
                <div class="proxy-pool-title">
                    <h2 class="control-title">Residential Proxy Pool Status</h2>
                    <span style="font-size: 0.8rem; font-weight: 500; color: var(--text-secondary);">IP Channels Autocheck active</span>
                </div>
                <div class="proxy-grid" id="proxy-list-grid">
                    <div style="color: var(--text-secondary); font-size: 0.9rem; grid-column: 1 / -1; text-align: center; padding: 1.5rem;" class="no-proxies-label">
                        No proxies configured. Running scraping sequence directly via local IP connection.
                    </div>
                </div>
            </div>

            <!-- Jobs Display List -->
            <div class="jobs-container">
                <div class="section-title">
                    <span>Scraped Jobs Feed</span>
                    <span style="font-size: 0.8rem; font-weight: 500; color: var(--text-secondary); margin-left: auto;" id="last-scrape-lbl">Last Scraped: Never</span>
                </div>
                <div id="jobs-list">
                    <div class="no-jobs">
                        <div class="empty-state-icon">
                            <svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="7" width="20" height="14" rx="2" ry="2"></rect><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"></path></svg>
                        </div>
                        <p>No job listings loaded. Execute a scrape sequence from the control panel to view results.</p>
                    </div>
                </div>
            </div>
        </div>

        <script>
            let isScraping = false;

            async function updateStatus() {
                try {
                    const response = await fetch('/status');
                    const data = await response.json();
                    
                    document.getElementById('stat-requests').innerText = data.total_requests;
                    document.getElementById('stat-rate').innerText = data.success_rate + '%';
                    document.getElementById('stat-proxies').innerText = data.active_proxies_count;
                    document.getElementById('stat-ratelimits').innerText = data.rate_limit_hits;

                    if (data.last_scrape_time) {
                        const date = new Date(data.last_scrape_time);
                        document.getElementById('last-scrape-lbl').innerText = 'Last Scraped: ' + date.toLocaleTimeString();
                    }

                    // Populate proxy grid
                    const proxyGrid = document.getElementById('proxy-list-grid');
                    if (data.proxies && data.proxies.length > 0) {
                        proxyGrid.innerHTML = '';
                        data.proxies.forEach(proxy => {
                            const item = document.createElement('div');
                            item.className = 'proxy-card';
                            const latStr = proxy.latency_ms ? `${proxy.latency_ms.toFixed(0)}ms` : 'N/A';
                            item.innerHTML = `
                                <div class="proxy-info">
                                    <span class="proxy-ip">${proxy.address}</span>
                                    <span class="proxy-latency">
                                        <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 2 7 12 12 22 7 12 2"></polygon><polyline points="2 17 12 22 22 17"></polyline><polyline points="2 12 12 17 22 12"></polyline></svg>
                                        Latency: ${latStr}
                                    </span>
                                </div>
                                <span class="proxy-badge ${proxy.is_alive ? 'badge-active' : 'badge-dead'}">
                                    ${proxy.is_alive ? 'ACTIVE' : 'DEAD'}
                                </span>
                            `;
                            proxyGrid.appendChild(item);
                        });
                    } else {
                        proxyGrid.innerHTML = '<div style="color: var(--text-secondary); font-size: 0.9rem; grid-column: 1 / -1; text-align: center; padding: 1.5rem;">No proxies configured. Running scraping sequence directly via local IP connection.</div>';
                    }
                } catch (e) {
                    console.error("Failed to load status updates", e);
                }
            }

            async function refreshLogs() {
                try {
                    const response = await fetch('/logs');
                    const data = await response.json();
                    const terminal = document.getElementById('log-terminal');
                    terminal.innerHTML = '';
                    data.logs.forEach(log => {
                        const div = document.createElement('div');
                        div.className = 'log-line';
                        
                        // Apply styling based on log contents
                        if (log.includes('ERROR:')) {
                            div.className += ' log-error';
                        } else if (log.includes('WARNING:')) {
                            div.className += ' log-warning';
                        } else if (log.includes('CRITICAL:')) {
                            div.className += ' log-error';
                        } else if (log.includes('SYSTEM:')) {
                            div.className += ' log-system';
                        } else if (log.includes('Successfully scraped') || log.includes('passed')) {
                            div.className += ' log-success';
                        } else {
                            div.className += ' log-info';
                        }
                        
                        div.innerText = log;
                        terminal.appendChild(div);
                    });
                    terminal.scrollTop = terminal.scrollHeight;
                } catch(e) {
                    console.error("Failed to load logs", e);
                }
            }

            async function triggerScrape() {
                if (isScraping) return;
                
                const source = document.getElementById('scrape-source').value;
                const btn = document.getElementById('btn-scrape-trigger');
                const btnText = document.getElementById('btn-text');

                isScraping = true;
                btn.disabled = true;
                btnText.innerHTML = '<span class="loader"></span>Sequence Active...';

                // Add prompt line to log terminal
                const terminal = document.getElementById('log-terminal');
                const timeStr = new Date().toLocaleTimeString();
                terminal.innerHTML += `<div class="log-line log-system">[${timeStr}] SYSTEM: User initiated scrape query for source: '${source}'</div>`;
                terminal.scrollTop = terminal.scrollHeight;

                try {
                    const response = await fetch(`/scrape?source=${source}`);
                    const data = await response.json();

                    // Render jobs
                    const jobsList = document.getElementById('jobs-list');
                    if (data.jobs && data.jobs.length > 0) {
                        jobsList.innerHTML = '';
                        data.jobs.forEach(job => {
                            const item = document.createElement('div');
                            item.className = 'job-card';
                            
                            // Left border color accents per source
                            if (source === 'github') {
                                item.style.borderLeftColor = 'var(--accent-purple)';
                            } else if (source === 'stackoverflow') {
                                item.style.borderLeftColor = 'var(--accent-orange)';
                            } else {
                                item.style.borderLeftColor = 'var(--accent-blue)';
                            }

                            item.innerHTML = `
                                <div class="job-header">
                                    <div>
                                        <div class="job-title">${job.title}</div>
                                        <div class="job-company">${job.company}</div>
                                    </div>
                                    <div class="job-location-badge">
                                        <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path><circle cx="12" cy="10" r="3"></circle></svg>
                                        ${job.location}
                                    </div>
                                </div>
                                <p class="job-desc">${job.description_snippet || 'No job details provided. Select apply link below to view requirements.'}</p>
                                <div class="job-footer">
                                    <span class="job-date">
                                        <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect><line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" y2="6"></line><line x1="3" y1="10" x2="21" y2="10"></line></svg>
                                        Published: ${job.posted_date}
                                    </span>
                                    <a class="btn-apply" href="${job.url}" target="_blank">Apply Now &rarr;</a>
                                </div>
                            `;
                            jobsList.appendChild(item);
                        });
                    } else {
                        jobsList.innerHTML = `
                            <div class="no-jobs">
                                <div class="empty-state-icon">
                                    <svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="7.86 2 16.14 2 22 7.86 22 16.14 16.14 22 7.86 22 2 16.14 2 7.86 7.86 2"></polygon><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>
                                </div>
                                <p>Failed to retrieve active job listings. Inspect terminal logs to diagnose network or proxy blocks.</p>
                            </div>`;
                    }
                } catch (e) {
                    terminal.innerHTML += `<div class="log-line log-error">[${new Date().toLocaleTimeString()}] ERROR: Scrape request network error! ${e.message}</div>`;
                } finally {
                    isScraping = false;
                    btn.disabled = false;
                    btnText.innerText = 'Execute Scrape Sequence';
                    await updateStatus();
                    await refreshLogs();
                }
            }

            // Polling metrics and logs
            setInterval(updateStatus, 3000);
            setInterval(refreshLogs, 1500);

            // Initial load
            updateStatus();
            refreshLogs();
        </script>
    </body>
    </html>
    """
    return html_content

