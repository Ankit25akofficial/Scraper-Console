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
