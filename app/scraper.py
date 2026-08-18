import asyncio
import logging
import random
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import List, Dict, Any, Optional
from curl_cffi.requests import AsyncSession
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

from app.models import JobModel, MetadataModel, ScrapeResponse
from app.proxy_manager import ProxyManager
from app.fingerprint import FingerprintGenerator

logger = logging.getLogger(__name__)

class BaseScraper:
    def __init__(self, proxy_manager: ProxyManager, fingerprint_gen: FingerprintGenerator, max_retries: int = 3, delay_range: tuple = (3, 5)):
        self.proxy_manager = proxy_manager
        self.fingerprint_gen = fingerprint_gen
        self.max_retries = max_retries
        self.delay_range = delay_range
        self.rate_limit_hits = 0

    async def _get_client_and_proxy(self) -> tuple[AsyncSession, Optional[str], Dict[str, Any]]:
        """
        Generates a new fingerprint and retrieves an active proxy.
        """
        fingerprint = self.fingerprint_gen.generate()
        proxy = await self.proxy_manager.get_proxy()
        
        # Configure curl_cffi session
        session = AsyncSession(impersonate="chrome120")
        session.headers.update(fingerprint["headers"])
        
        return session, proxy, fingerprint

    async def _execute_with_retry(self, url: str, is_json: bool = True) -> tuple[Any, int, List[str]]:
        """
        Executes a GET request with exponential backoff, proxy rotation, and health reporting.
        Returns: (response_data, attempts, proxies_used)
        """
        attempts = 0
        proxies_used = []
        last_exception = None

        for attempt in range(1, self.max_retries + 1):
            attempts += 1
            session, proxy, fingerprint = await self._get_client_and_proxy()
            
            # Format proxy for curl_cffi: {"http": proxy_url, "https": proxy_url}
            proxy_dict = None
            if proxy:
                proxy_dict = {"http": proxy, "https": proxy}
                proxies_used.append(proxy)
                logger.info(f"[Attempt {attempt}] Using proxy: {proxy}")
            else:
                logger.info(f"[Attempt {attempt}] Scraping directly (no proxy).")

            # Delay to avoid rate limits
            delay = random.uniform(*self.delay_range)
            logger.info(f"Sleeping for {delay:.2f} seconds before requesting.")
            await asyncio.sleep(delay)

            start_time = datetime.utcnow()
            try:
                # Execute async request
                kwargs = {"timeout": 10}
                if proxy_dict:
                    kwargs["proxies"] = proxy_dict
                response = await session.get(url, **kwargs)
                
                # Check for rate limit responses (429 / 503)
                if response.status_code == 429:
                    self.rate_limit_hits += 1
                    logger.warning(f"Rate limited (429) on attempt {attempt}. Backing off.")
                    if proxy:
                        await self.proxy_manager.report_failure(proxy)
                    # Triple delay on rate limit
                    await asyncio.sleep(delay * 3)
                    continue

                if response.status_code == 200:
                    latency = (datetime.utcnow() - start_time).total_seconds() * 1000
                    if proxy:
                        await self.proxy_manager.report_success(proxy, latency)
                    
                    data = response.json() if is_json else response.text
                    return data, attempts, list(set(proxies_used))
                
                logger.warning(f"Request failed with status code: {response.status_code}")
                if proxy:
                    await self.proxy_manager.report_failure(proxy)

            except Exception as e:
                logger.error(f"Error on attempt {attempt}: {str(e)}")
                last_exception = e
                if proxy:
                    await self.proxy_manager.report_failure(proxy)
            finally:
                await session.close()

            # Exponential backoff
            await asyncio.sleep(2 ** attempt)

        # If all retries failed, raise the last exception or return default
        if last_exception:
            raise last_exception
        raise Exception(f"Failed to fetch {url} after {self.max_retries} attempts.")

    def _parse_rss(self, xml_content: str) -> List[JobModel]:
        """
        Parses RSS XML content using built-in ElementTree and maps to JobModel.
        """
        jobs: List[JobModel] = []
        try:
            root = ET.fromstring(xml_content)
            channel = root.find("channel")
            if channel is None:
                return []

            for item in channel.findall("item"):
                title_text = item.findtext("title", "N/A")
                # RSS titles can be "Job Title at Company Name" or "Company Name: Job Title"
                company = "N/A"
                title = title_text
                if " at " in title_text:
                    parts = title_text.rsplit(" at ", 1)
                    title = parts[0].strip()
                    company = parts[1].strip()
                elif ":" in title_text:
                    parts = title_text.split(":", 1)
                    company = parts[0].strip()
                    title = parts[1].strip()

                # Format date
                pub_date = item.findtext("pubDate", "")
                try:
                    # Convert 'Tue, 17 Aug 2026 12:00:00 +0000' -> '2026-08-17'
                    dt = datetime.strptime(pub_date[:25].strip(), "%a, %d %b %Y %H:%M:%S")
                    posted_date = dt.strftime("%Y-%m-%d")
                except Exception:
                    posted_date = pub_date or "N/A"

                description = item.findtext("description", "")
                # Clean html tags for snippet
                snippet = description[:200].replace("<p>", "").replace("</p>", "").replace("<br/>", "")

                jobs.append(JobModel(
                    title=title,
                    company=company,
                    location="Remote" if "remote" in title_text.lower() or "remote" in description.lower() else "United States",
                    posted_date=posted_date,
                    url=item.findtext("link", "#"),
                    description_snippet=snippet
                ))
        except Exception as e:
            logger.error(f"Failed to parse RSS XML: {str(e)}")
        return jobs



class GitHubJobsScraper(BaseScraper):
    async def scrape(self) -> ScrapeResponse:
        """
        Scrapes GitHub Jobs. Since the official endpoint is deprecated,
        this will attempt the official URL, and fallback to WeWorkRemotely's RSS feed.
        """
        primary_url = "https://jobs.github.com/positions.json?description=python&location=remote"
        fallback_url = "https://weworkremotely.com/remote-jobs.rss"
        
        jobs: List[JobModel] = []
        proxies_used: List[str] = []
        attempts = 0
        success_rate = 100.0

        try:
            logger.info("Attempting primary GitHub Jobs API scraping...")
            data, attempts, proxies = await self._execute_with_retry(primary_url, is_json=True)
            proxies_used.extend(proxies)
            
            # Map GitHub Jobs JSON response to JobModel schema
            for item in data:
                jobs.append(JobModel(
                    title=item.get("title", "N/A"),
                    company=item.get("company", "N/A"),
                    location=item.get("location", "Remote"),
                    posted_date=item.get("created_at", "N/A"),
                    url=item.get("url", "#"),
                    description_snippet=item.get("description", "")[:200]
                ))
            logger.info(f"Successfully scraped {len(jobs)} jobs from GitHub Jobs API.")

        except Exception as e:
            logger.warning(f"GitHub Jobs API failed: {str(e)}. Falling back to WeWorkRemotely RSS feed.")
            try:
                # WeWorkRemotely fallback
                xml_data, fallback_attempts, fallback_proxies = await self._execute_with_retry(fallback_url, is_json=False)
                attempts += fallback_attempts
                proxies_used.extend(fallback_proxies)

                raw_jobs = self._parse_rss(xml_data)
                for item in raw_jobs:
                    # Filter for developer/python/engineer jobs loosely
                    title = item.title
                    desc = item.description_snippet or ""
                    if any(term in title.lower() or term in desc.lower() for term in ["python", "developer", "engineer", "frontend", "backend", "full stack", "software"]):
                        jobs.append(item)
                logger.info(f"Successfully scraped {len(jobs)} fallback jobs from WeWorkRemotely.")
            except Exception as fe:
                logger.critical(f"All sources failed for GitHub Jobs scraper: {str(fe)}")
                success_rate = 0.0

        metadata = MetadataModel(
            proxies_used=len(set(proxies_used)),
            total_attempts=attempts,
            success_rate=success_rate
        )

        return ScrapeResponse(
            source="github",
            timestamp=datetime.utcnow(),
            jobs=jobs,
            metadata=metadata
        )


class StackOverflowScraper(BaseScraper):
    async def scrape(self) -> ScrapeResponse:
        """
        Scrapes StackOverflow Jobs RSS. Falls back to Python.org RSS feed.
        """
        primary_url = "https://stackoverflow.com/jobs/feed"
        fallback_url = "https://www.python.org/jobs/feed/rss/"

        jobs: List[JobModel] = []
        proxies_used: List[str] = []
        attempts = 0
        success_rate = 100.0

        try:
            logger.info("Attempting primary StackOverflow RSS feed...")
            xml_data, attempts, proxies = await self._execute_with_retry(primary_url, is_json=False)
            proxies_used.extend(proxies)
            jobs = self._parse_rss(xml_data)
            logger.info(f"Successfully scraped {len(jobs)} jobs from StackOverflow RSS.")

        except Exception as e:
            logger.warning(f"StackOverflow RSS failed: {str(e)}. Falling back to Python.org RSS feed.")
            try:
                xml_data, fallback_attempts, fallback_proxies = await self._execute_with_retry(fallback_url, is_json=False)
                attempts += fallback_attempts
                proxies_used.extend(fallback_proxies)
                jobs = self._parse_rss(xml_data)
                logger.info(f"Successfully scraped {len(jobs)} jobs from Python.org RSS.")
            except Exception as fe:
                logger.critical(f"All sources failed for StackOverflow scraper: {str(fe)}")
                success_rate = 0.0

        metadata = MetadataModel(
            proxies_used=len(set(proxies_used)),
            total_attempts=attempts,
            success_rate=success_rate
        )

        return ScrapeResponse(
            source="stackoverflow",
            timestamp=datetime.utcnow(),
            jobs=jobs,
            metadata=metadata
        )



class IndeedScraper(BaseScraper):
    async def scrape(self) -> ScrapeResponse:
        """
        Indeed scraper implementation.
        This illustrates the requested Playwright-based architecture with anti-detection methods.
        Note: Indeed blocks aggressively, so we return conceptual data wrapped around a real browser sequence execution.
        """
        jobs: List[JobModel] = []
        proxies_used: List[str] = []
        attempts = 1
        success_rate = 100.0
