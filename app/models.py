from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

class JobModel(BaseModel):
    title: str = Field(..., description="The job title")
    company: str = Field(..., description="The hiring company name")
    location: str = Field(..., description="The job location (e.g. Remote, City, State)")
    posted_date: str = Field(..., description="The date the job was posted (YYYY-MM-DD or relative description)")
    url: str = Field(..., description="The direct URL to apply or view the listing")
    description_snippet: Optional[str] = Field(None, description="A brief snippet of the job description")

class MetadataModel(BaseModel):
    proxies_used: int = Field(default=0, description="The number of unique proxies attempted during the request")
    total_attempts: int = Field(default=1, description="Total scrape attempts (including retries)")
    success_rate: float = Field(default=100.0, description="Percentage of successful attempts in proxy usage")

class ScrapeResponse(BaseModel):
    source: str = Field(..., description="The source requested (github, stackoverflow, indeed)")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="ISO-8601 timestamp of completion")
    jobs: List[JobModel] = Field(default_factory=list, description="List of jobs scraped")
    metadata: MetadataModel = Field(..., description="Metadata related to proxy usage and attempts")

class HealthResponse(BaseModel):
    status: str = Field("healthy", description="API health status")
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class ProxyStatus(BaseModel):
    address: str
    is_alive: bool
    fail_count: int
    latency_ms: Optional[float] = None

class StatusResponse(BaseModel):
    uptime_seconds: float
    total_requests: int
    successful_requests: int
    failed_requests: int
    success_rate: float
    active_proxies_count: int
    proxies: List[ProxyStatus]
    last_scrape_time: Optional[datetime] = None
    rate_limit_hits: int = 0
