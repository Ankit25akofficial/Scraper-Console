# Architectural Decisions - Acdyon Scraper

This document outlines key technical decisions, trade-offs, and verification workflows for the Acdyon Web Scraper.

## 1. Why Playwright + Stealth over Alternatives?

| Scraping Tool | Dynamic JS Rendering | Anti-Bot Stealth | Performance / Overhead | Verdict |
| :--- | :--- | :--- | :--- | :--- |
| **Requests / httpx** | No | Poor (TLS Fingerprint blocks) | Low CPU/RAM | Fail (Required for Indeed/JS sites) |
| **Selenium** | Yes | Fair (Webdriver leakages) | Medium CPU/RAM | Passable, but legacy & slower |
| **Playwright + Stealth** | **Yes** | **Excellent (Hides automation markers)** | **High (Async, fast startup)** | **Selected** |

### Rationale
- **Stealth Bypasses**: Modern scrapers fail because of webdriver detection. The `playwright-stealth` library removes global variables like `navigator.webdriver` and simulates natural browser features (plugins, dimensions) which Selenium lacks out of the box.
- **Resource Management**: Playwright handles page events asynchronously, reducing memory footprint compared to selenium.
- **Dual-Engine Approach**: For API/RSS endpoints (GitHub/SO), we bypassed browser launch entirely and used `curl_cffi` (mimicking Chrome 120 TLS fingerprinting). We reserved Playwright for Indeed, achieving high execution speed.

---

## 2. Trade-offs Made Under the Time Limit

### The Trade-off: Fallback Feeds vs. Live DOM Scrapers for Discontinued APIs
- **Context**: The official GitHub Jobs API and StackOverflow RSS endpoints are defunct.
- **Decision**: Instead of writing complex dummy static parsers for URLs that return 404s, we wrote scraping logic targeting the specified endpoints first, but immediately implemented automatic, silent fallbacks to live, equivalent platforms (**WeWorkRemotely API** and **Python.org Jobs RSS**).
- **If I Had a Week**: 
  1. I would implement a distributed queue system using Celery and Redis to handle job deduplication across multiple scraping nodes.
  2. I would build a visual dashboard monitoring IP ban rates per proxy provider.
  3. I would deploy a dedicated CAPTCHA solver microservice integrating CapSolver's API to handle hard-blocked indeed/Cloudflare challenges automatically.

---

## 3. AI Usage and Verification

### What AI Handled
- Generating the Pydantic schemas.
- Boilerplate setup for the FastAPI structure, Uvicorn settings, and the custom Vanilla CSS theme for the dashboard.
- Creating mock job templates for Indeed.

### What was Manually/Locally Verified
- **Proxy Rotation Logic**: Verified that failures rotate to a new proxy and that 3 consecutive failures successfully mark a proxy as dead in the status response.
- **TLS Spoofing**: Inspected curl_cffi header output to confirm that it matches Chrome 120 signatures.
- **FastAPI Routing & Exception Handlers**: Verified that calling `/scrape?source=invalid` correctly returns a 400 Bad Request, and verified background task execution of proxy health checks.
