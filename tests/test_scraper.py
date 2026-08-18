import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app.main import app, proxy_manager, fingerprint_gen
from app.models import JobModel, ScrapeResponse, MetadataModel

client = TestClient(app)

def test_health_endpoint():
    """Verify that the health check endpoint returns 200 and indicates healthy status."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "timestamp" in data

def test_status_endpoint():
    """Verify that status endpoint aggregates scraper metrics and proxy statuses correctly."""
    response = client.get("/status")
    assert response.status_code == 200
    data = response.json()
    assert "uptime_seconds" in data
    assert "success_rate" in data
    assert "active_proxies_count" in data
    assert "proxies" in data

def test_fingerprint_generator():
    """Ensure browser fingerprint generator outputs realistic headers, viewport, and timezone parameters."""
    profile = fingerprint_gen.generate()
    assert "user_agent" in profile
    assert "viewport" in profile
    assert "locale" in profile
    assert "timezone" in profile
    assert "headers" in profile
    
    # Check header parameters
    headers = profile["headers"]
    assert "User-Agent" in headers
    assert "Accept-Language" in headers
    assert "Sec-Ch-Ua" in headers

@pytest.mark.asyncio
async def test_proxy_manager_rotation():
    """Verify that proxy manager rotates IPs correctly and flags dead proxies."""
    from app.proxy_manager import ProxyManager
    pm = ProxyManager("http://proxy1:8000,http://proxy2:8000")
    
    # Test getting proxies in round-robin sequence
    p1 = await pm.get_proxy()
    p2 = await pm.get_proxy()
    assert p1 != p2
    
    # Test report failure and recovery
    await pm.report_failure(p1)
    await pm.report_failure(p1)
    await pm.report_failure(p1)
    
    # After 3 failures, p1 should be deactivated
    statuses = pm.get_all_statuses()
    p1_status = next(s for s in statuses if s["address"] == p1)
    assert p1_status["is_alive"] is False
    
    # Next proxy selection should skip dead proxy and yield active p2
    next_p = await pm.get_proxy()
    assert next_p == p2

@patch("app.main.GitHubJobsScraper.scrape", new_callable=AsyncMock)
def test_scrape_github_endpoint(mock_scrape):
    """Verify GitHub jobs scraping endpoint outputs mapped jobs data correctly."""
    # Setup mock return value
    mock_scrape.return_value = ScrapeResponse(
        source="github",
        jobs=[
            JobModel(
                title="Mock Python Engineer",
                company="Mock Corp",
                location="Remote",
                posted_date="2026-08-18",
                url="https://github.com/mock-job",
                description_snippet="Looking for a Python Developer..."
            )
        ],
        metadata=MetadataModel(proxies_used=0, total_attempts=1, success_rate=100.0)
    )

    response = client.get("/scrape?source=github")
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "github"
    assert len(data["jobs"]) == 1
    assert data["jobs"][0]["title"] == "Mock Python Engineer"
    assert data["metadata"]["success_rate"] == 100.0

@patch("app.main.StackOverflowScraper.scrape", new_callable=AsyncMock)
def test_scrape_stackoverflow_endpoint(mock_scrape):
    """Verify StackOverflow RSS jobs scraping endpoint handles successful parsing calls."""
    mock_scrape.return_value = ScrapeResponse(
        source="stackoverflow",
        jobs=[
            JobModel(
                title="Django Specialist",
                company="Web Services Ltd",
                location="Remote",
                posted_date="2026-08-17",
                url="https://stackoverflow.com/mock-job",
                description_snippet="Django/FastAPI developer wanted."
            )
        ],
        metadata=MetadataModel(proxies_used=1, total_attempts=1, success_rate=100.0)
    )

    response = client.get("/scrape?source=stackoverflow")
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "stackoverflow"
    assert len(data["jobs"]) == 1
    assert data["jobs"][0]["company"] == "Web Services Ltd"

@patch("app.main.IndeedScraper.scrape", new_callable=AsyncMock)
def test_scrape_indeed_endpoint(mock_scrape):
    """Verify Indeed endpoint delegates to Playwright-stealth scraper sequence."""
    mock_scrape.return_value = ScrapeResponse(
        source="indeed",
        jobs=[
            JobModel(
                title="Indeed Fullstack Python Developer",
                company="Big Tech Group",
                location="Remote",
                posted_date="2026-08-15",
                url="https://indeed.com/mock-job",
                description_snippet="We need a Python developer who knows React."
            )
        ],
        metadata=MetadataModel(proxies_used=1, total_attempts=1, success_rate=100.0)
    )

    response = client.get("/scrape?source=indeed")
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "indeed"
    assert len(data["jobs"]) == 1
    assert data["jobs"][0]["title"] == "Indeed Fullstack Python Developer"

def test_invalid_scrape_source():
    """Verify scraping endpoint rejects unsupported sources with a 400 Bad Request."""
    response = client.get("/scrape?source=linkedin")
    assert response.status_code == 400
    assert "Invalid source" in response.json()["detail"]
