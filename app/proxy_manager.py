import time
import logging
import asyncio
from typing import List, Dict, Any, Optional
from curl_cffi import requests

logger = logging.getLogger(__name__)

class ProxyState:
    def __init__(self, address: str):
        self.address = address
        self.is_alive = True
        self.fail_count = 0
        self.latency_ms = None
        self.consecutive_success = 0

class ProxyManager:
    def __init__(self, proxy_list_str: Optional[str] = None):
        self.proxies: List[ProxyState] = []
        if proxy_list_str:
            # Parse comma-separated list of proxies
            addresses = [p.strip() for p in proxy_list_str.split(",") if p.strip()]
            self.proxies = [ProxyState(addr) for addr in addresses]
            logger.info(f"Loaded {len(self.proxies)} proxies into the ProxyManager.")
        else:
            logger.warning("No proxies configured. Scraper will run without proxy rotation.")

        self._current_index = 0
        self._lock = asyncio.Lock()

    async def get_proxy(self) -> Optional[str]:
        """
        Gets the next healthy proxy (Round Robin style, skipping dead ones).
        Returns None if no proxies are configured or all proxies are dead.
        """
        async with self._lock:
            if not self.proxies:
                return None

            total = len(self.proxies)
            # Scan the list to find a healthy proxy
            for _ in range(total):
                proxy = self.proxies[self._current_index]
                self._current_index = (self._current_index + 1) % total
                if proxy.is_alive:
                    return proxy.address

            # If all are dead, try to return any proxy that has the lowest fail count as a fallback,
            # or try to force a health check
            alive_proxies = [p for p in self.proxies if p.is_alive]
            if not alive_proxies:
                logger.error("All proxies are marked dead! Attempting to use the best failed proxy.")
                sorted_failed = sorted(self.proxies, key=lambda p: p.fail_count)
                if sorted_failed:
                    return sorted_failed[0].address
            
            return None

    async def report_success(self, proxy_address: str, latency_ms: float):
        """
        Reports a successful request using a proxy, updating health stats.
        """
        for p in self.proxies:
            if p.address == proxy_address:
                p.fail_count = 0
                p.latency_ms = latency_ms
                p.consecutive_success += 1
                if not p.is_alive:
                    p.is_alive = True
                    logger.info(f"Proxy {proxy_address} recovered and is marked ALIVE.")
                break

    async def report_failure(self, proxy_address: str):
        """
        Reports a failure using a proxy. Deactivates it if failure count is too high.
        """
        for p in self.proxies:
            if p.address == proxy_address:
                p.fail_count += 1
                p.consecutive_success = 0
                logger.warning(f"Proxy {proxy_address} failed. Total failures: {p.fail_count}")
                if p.fail_count >= 3:
                    p.is_alive = False
                    logger.critical(f"Proxy {proxy_address} has failed 3+ times. Marking as DEAD.")
                break

    async def check_proxy_health(self, proxy: ProxyState) -> bool:
        """
        Asynchronously checks the health of a single proxy by making a request to httpbin.org.
        """
        test_url = "https://httpbin.org/ip"
        start_time = time.time()
        proxies_dict = {"http": proxy.address, "https": proxy.address}
        
        try:
            # We run this check in an executor to avoid blocking the event loop
            loop = asyncio.get_event_loop()
            
            def run_request():
                return requests.get(
                    test_url,
                    proxies=proxies_dict,
                    timeout=5,
                    impersonate="chrome120"
                )
                
            response = await loop.run_in_executor(None, run_request)
            
            if response.status_code == 200:
                latency = (time.time() - start_time) * 1000
                proxy.is_alive = True
                proxy.fail_count = 0
                proxy.latency_ms = latency
                logger.debug(f"Proxy Health Check Passed: {proxy.address} (Latency: {latency:.1f}ms)")
                return True
        except Exception as e:
            logger.debug(f"Proxy Health Check Failed: {proxy.address} - Error: {str(e)}")
            
        proxy.fail_count += 1
        if proxy.fail_count >= 3:
            proxy.is_alive = False
        return False

    async def check_all_proxies(self) -> List[Dict[str, Any]]:
        """
        Runs health checks on all proxies in parallel and returns their status.
        """
        if not self.proxies:
            return []
        
        tasks = [self.check_proxy_health(p) for p in self.proxies]
        await asyncio.gather(*tasks)
        return self.get_all_statuses()

    def get_all_statuses(self) -> List[Dict[str, Any]]:
        """
        Returns a list of dictionary representations of the proxy statuses.
        """
        return [
            {
                "address": p.address,
                "is_alive": p.is_alive,
                "fail_count": p.fail_count,
                "latency_ms": p.latency_ms
            }
            for p in self.proxies
        ]
