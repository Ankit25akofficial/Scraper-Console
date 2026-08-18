import random
from typing import Dict, Any, List

# Standard viewport sizes requested
VIEWPORTS = [
    {"width": 1366, "height": 1080},
    {"width": 1440, "height": 900},
    {"width": 1920, "height": 1080}
]

# Recent Chrome user agents
DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
]

LOCALES = ["en-US", "en-GB", "en-CA"]

# Timezones corresponding to English-speaking regions
TIMEZONES = [
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "Europe/London",
    "America/Toronto",
    "America/Vancouver"
]

class FingerprintGenerator:
    def __init__(self, custom_user_agents: List[str] = None):
        self.user_agents = custom_user_agents if custom_user_agents else DEFAULT_USER_AGENTS

    def generate(self) -> Dict[str, Any]:
        """
        Generates a random browser fingerprint profile.
        """
        user_agent = random.choice(self.user_agents)
        viewport = random.choice(VIEWPORTS)
        locale = random.choice(LOCALES)
        timezone = random.choice(TIMEZONES)

        # Chrome structured user-agent hints and header construction
        headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": f"{locale},{locale.split('-')[0]};q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"' if "120" in user_agent else '"Not_A Brand";v="8", "Chromium";v="121", "Google Chrome";v="121"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"' if "Windows" in user_agent else ('"macOS"' if "Macintosh" in user_agent else '"Linux"'),
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        }

        return {
            "user_agent": user_agent,
            "viewport": viewport,
            "locale": locale,
            "timezone": timezone,
            "headers": headers
        }
