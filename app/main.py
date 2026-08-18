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

