# Phase 3 — Job Scraper Engine

Crawls job portals on a schedule, saves new listings, and handles anti-bot systems.

## Goals

- Run continuously (Celery beat + worker)
- Scrape major portals with human-like pacing
- Stealth browser sessions to reduce blocks
- Upsert normalized jobs into Postgres

## Steps

| # | Step | Status |
|---|------|--------|
| 1 | Base scraper class with stealth + human delays | **Done** |
| 2 | Naukri.com scraper | **Done** |
| 3 | LinkedIn scraper | **Done** |
| 4 | Persist scraped jobs + dedupe | **Done** (external_id + url fingerprint) |
| 5 | Celery schedule every 2 hours + `/scanner/trigger` | **Done** |
| 6 | Portal expansion: Internshala, Foundit, Unstop | **Done** |
| 7 | Proxies, retries, captcha / block handling | Pending |

## Step 1 — Base scraper

Package: `workers/job-scanner/job_scanner/`

- **`BaseScraper`** — Playwright Chromium lifecycle (`async with`), shared navigation helpers
- **`stealth`** — rotating user agents / viewports / locales / timezones; hides `navigator.webdriver`
- **`delays`** — triangular-random pauses for navigate, action, scroll, page, think
- **`ScrapedJob`** — normalized output shape for later DB writes
- Block detection raises **`ScraperBlockedError`** on HTTP 401/403/429/503 or challenge page markers

Portal-specific scrapers subclass `BaseScraper` and implement `scrape() -> list[ScrapedJob]`.

## Step 2 — Naukri.com

Package: `workers/job-scanner/job_scanner/portals/naukri/`

- Builds SERP URLs like `/python-developer-jobs-in-bangalore` (+ `-2` for page 2)
- Prefers **installed Google Chrome** (`channel=chrome`) — bundled Chromium often gets an empty shell from Akamai
- Warms up on `naukri.com` homepage, then opens search pages (**headed by default**)
- **Primary:** intercepts Naukri’s public `jobapi/v3/search` JSON (same API the website uses)
- **Fallback:** parses job cards from the DOM if the XHR is missed
- Maps into `ScrapedJob` (`source="naukri"`, salary INR when present, skills in `raw_payload`)

**Requirement:** Google Chrome installed on the machine for reliable runs.

```powershell
cd workers/job-scanner
$env:PYTHONPATH = (Get-Location).Path
python -m job_scanner.portals.naukri -k "python developer" -l bangalore -p 1 -v
```

## Step 3 — LinkedIn

Package: `workers/job-scanner/job_scanner/portals/linkedin/`

- Public search URL: `/jobs/search?keywords=...&location=...`
- **Primary:** guest HTML API `jobs-guest/jobs/api/seeMoreJobPostings/search` (no login), paginated with `start=0,25,50...`
- **Fallback:** parse cards from the public jobs search DOM
- Dismisses common cookie / sign-in overlays best-effort
- Maps into `ScrapedJob` (`source="linkedin"`)

LinkedIn rate-limits aggressively; keep `max_pages` small for manual tests.

```powershell
cd workers/job-scanner
$env:PYTHONPATH = (Get-Location).Path
python -m job_scanner.portals.linkedin -k "python developer" -l bangalore -p 1 -v
```

## Step 4–5 — Persist + Celery every 2 hours

- Task: `app.tasks.job_scanner.scan_jobs`
- Beat: `crontab(minute=0, hour="*/2")` (00:00, 02:00, 04:00… UTC)
- Upserts into `jobs` on `(source, external_id)`
- Manual trigger: `POST /scanner/trigger` (auth required)
- Config via env: `SCANNER_KEYWORDS`, `SCANNER_LOCATIONS`, `SCANNER_PORTALS`, `SCANNER_ENABLED`, …

```powershell
# Rebuild worker (Playwright/Chromium) then restart beat
docker compose up -d --build celery-worker celery-beat backend

# Fire one scan now
docker compose exec celery-worker celery -A app.celery_app.celery call app.tasks.job_scanner.scan_jobs
```
