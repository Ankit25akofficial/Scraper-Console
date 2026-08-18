# Acdyon Web Scraper Service

A production-ready, resilient, and containerized web scraper built with Python, FastAPI, Playwright (with Stealth anti-detection), and curl_cffi (with TLS Chrome 120 fingerprinting) for the Acdyon Technologies Frontend Challenge (Part 1).

## 📹 Console Demo Video

Here is a live recording showing the scraper console dashboard in action, illustrating real-time logs, live scraping, fallback failover sequences, and dynamically populated jobs cards:

![Scraper Console Demo](assets/scraper_console_demo.webp)

## 🚀 Key Features
1. **Resilient Scrapers**:
   - **GitHub Jobs API**: Attempts the requested URL and falls back dynamically to the WeWorkRemotely JSON API.
   - **StackOverflow RSS**: Attempts the requested RSS URL and falls back dynamically to the Python.org XML jobs feed.
   - **Indeed (Conceptual)**: Uses Playwright with stealth settings, simulating mouse movements and keyboard scrolling to bypass anti-bot systems.
2. **Anti-Detection Measures**:
   - TLS Fingerprint Spoofing (via `curl_cffi` mimicking Chrome 120 client signatures).
   - Random Viewport dimensions, Locales (`en-US`, `en-CA`, `en-GB`), Timezones, and Google Chrome User-Agents.
   - Human behavior simulation (smooth scrolling, random cursor movements).
3. **Proxy Rotation & Management**:
   - Support for rotating residential proxies (comma-separated URLs).
   - Async health checking (pinging `httpbin.org/ip`) and failover.
   - Intelligent auto-rotation & deactivation (marks proxy dead after 3 consecutive failures).
4. **FastAPI Web Service & Status Dashboard**:
   - Interactive premium dark-mode dashboard (with Vanilla CSS) showing system status, proxy statuses, live scraping logs, and recent scraped job feeds.
   - JSON endpoints for health, scraper metrics, and scraping queries.

---

## 🛠️ Project Structure

```
acdyon-scraper/
├── app/
│   ├── __init__.py
│   ├── main.py           # FastAPI app, metrics tracker & dashboard router
│   ├── scraper.py        # Core scrapers (Base, GitHub, StackOverflow, Indeed)
│   ├── proxy_manager.py  # IP rotation and health management
│   ├── fingerprint.py    # Random user agents, viewports, locales, and headers
│   └── models.py         # Pydantic models for responses
├── tests/
│   └── test_scraper.py   # Pytest suite
├── Dockerfile            # Container deployment image
├── requirements.txt      # Python dependencies
├── .env.example          # Environment variables template
├── DECISIONS.md          # Architectural decisions & design trade-offs
└── README.md             # This file
```

---

## ⚙️ Configuration (Environment Variables)

Copy `.env.example` to `.env` and set the following parameters:

```ini
# List of proxies (comma-separated)
PROXY_LIST=http://user:pass@ip:port,socks5://user:pass@ip:port

# Custom User-Agents (comma-separated, optional)
USER_AGENT_LIST=Mozilla/5.0...

# Scraping settings
MAX_RETRIES=3
DELAY_RANGE=3,5
```

---

## 💻 Local Setup & Development

### 1. Prerequisite: Python 3.10+
Make sure Python is installed on your machine.

### 2. Install Dependencies & Playwright
```bash
# Initialize and activate virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install requirements
pip install -r requirements.txt

# Install Playwright browser dependencies
playwright install chromium
```

### 3. Run FastAPI Application
```bash
uvicorn app.main:app --reload
```
Navigate to [http://localhost:8000](http://localhost:8000) to view the interactive status dashboard.

### 4. Run Automated Test Suite
```bash
pytest tests/
```

---

## 🐳 Docker Deployment

### Build the Image
```bash
docker build -t acdyon-scraper .
```

### Run the Container
```bash
docker run -p 8000:8000 --env-file .env acdyon-scraper
```
Visit [http://localhost:8000](http://localhost:8000) in your browser.

---

## 📡 API Reference Documentation

### 1. Execute Scraper
* **Endpoint**: `GET /scrape?source={source}`
* **Query Parameters**: `source` (must be `github`, `stackoverflow`, or `indeed`).
* **Response**:
```json
{
  "source": "github",
  "timestamp": "2026-08-18T10:30:00.123456",
  "jobs": [
    {
      "title": "Senior Python Developer",
      "company": "Tech Corp",
      "location": "Remote",
      "posted_date": "2026-08-15",
      "url": "https://weworkremotely.com/...",
      "description_snippet": "We are looking for..."
    }
  ],
  "metadata": {
    "proxies_used": 1,
    "total_attempts": 1,
    "success_rate": 100
  }
}
```

### 2. Status Metrics
* **Endpoint**: `GET /status`
* **Response**: Returns scraper metrics, success rates, active proxies count, and full proxy pool status details.

### 3. Health Check
* **Endpoint**: `GET /health`
* **Response**: `{"status": "healthy", "timestamp": "..."}`
