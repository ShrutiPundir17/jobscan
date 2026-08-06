from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlencode, urljoin

from job_scanner.base import BaseScraper
from job_scanner.exceptions import ScraperBlockedError
from job_scanner.models import ScrapedJob

logger = logging.getLogger(__name__)

BASE_URL = "https://www.foundit.in"


def build_search_url(
    keyword: str,
    location: str | None = None,
    *,
    start: int = 0,
    limit: int = 20,
) -> str:
    params: dict[str, str] = {
        "query": keyword.strip(),
        "start": str(max(0, start)),
        "limit": str(limit),
        "sort": "1",
    }
    if location:
        params["locations"] = location.strip()
    return f"{BASE_URL}/srp/results?{urlencode(params)}"


class FounditScraper(BaseScraper):
    """Foundit.in (formerly Monster India) public SRP scraper."""

    name = "foundit"
    source = "foundit"

    def __init__(
        self,
        *,
        keyword: str,
        location: str | None = None,
        max_pages: int = 1,
        results_per_page: int = 20,
        headless: bool = False,
        browser_channel: str | None = "chrome",
        **kwargs: Any,
    ) -> None:
        super().__init__(headless=headless, browser_channel=browser_channel, **kwargs)
        self.keyword = keyword.strip()
        self.location = location.strip() if location else None
        self.max_pages = max(1, max_pages)
        self.results_per_page = max(1, results_per_page)
        self.search_url = build_search_url(self.keyword, self.location)
        self._api_jobs: list[dict[str, Any]] = []

    def context_options(self) -> dict[str, Any]:
        return {
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "viewport": {"width": 1366, "height": 768},
            "locale": "en-IN",
            "timezone_id": "Asia/Kolkata",
            "java_script_enabled": True,
            "extra_http_headers": {"Accept-Language": "en-IN,en;q=0.9"},
        }

    async def scrape(self) -> list[ScrapedJob]:
        self._api_jobs.clear()
        self.page.on("response", lambda r: __import__("asyncio").create_task(self._capture(r)))

        await self.goto(BASE_URL + "/")
        await self.human_pause("navigate")

        jobs: list[ScrapedJob] = []
        for page_num in range(self.max_pages):
            start = page_num * self.results_per_page
            before = len(self._api_jobs)
            url = build_search_url(
                self.keyword, self.location, start=start, limit=self.results_per_page
            )
            logger.info("foundit_page page=%s url=%s", page_num + 1, url)
            await self.goto(url)
            await self.scroll_down(900)

            for _ in range(24):
                if len(self._api_jobs) > before:
                    break
                await self.page.wait_for_timeout(500)

            new_items = self._api_jobs[before:]
            if new_items:
                jobs.extend(self._jobs_from_api(new_items))
            else:
                dom_jobs = await self._jobs_from_dom()
                jobs.extend(dom_jobs)
                if not dom_jobs:
                    break
            await self.human_pause("page")

        deduped = self._dedupe(jobs)
        logger.info("foundit_done count=%s", len(deduped))
        return deduped

    async def _capture(self, response) -> None:
        try:
            url = response.url.lower()
            if response.status != 200:
                return
            if "jobsearch" not in url and "srp" not in url and "middleware" not in url:
                return
            ctype = (response.headers or {}).get("content-type", "")
            if "json" not in ctype and "javascript" not in ctype:
                return
            data = await response.json()
            items = self._extract_items(data)
            if items:
                self._api_jobs.extend(items)
                logger.info("foundit_api_hit jobs=%s total=%s", len(items), len(self._api_jobs))
        except Exception:  # noqa: BLE001
            return

    def _extract_items(self, data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict) and (x.get("title") or x.get("jobTitle"))]
        if not isinstance(data, dict):
            return []
        for key in ("jobSearchResponse", "data", "jobs", "results", "jobList"):
            val = data.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
            if isinstance(val, dict):
                for nested in ("jobs", "data", "results", "jobData"):
                    inner = val.get(nested)
                    if isinstance(inner, list):
                        return [x for x in inner if isinstance(x, dict)]
        return []

    def _jobs_from_api(self, items: list[dict[str, Any]]) -> list[ScrapedJob]:
        jobs: list[ScrapedJob] = []
        for item in items:
            title = (item.get("title") or item.get("jobTitle") or "").strip()
            company_raw = item.get("companyName") or item.get("company") or "Unknown"
            if isinstance(company_raw, dict):
                company = str(company_raw.get("name") or "Unknown").strip() or "Unknown"
            else:
                company = str(company_raw).strip() or "Unknown"
            if not title:
                continue

            raw_url = (
                item.get("jobUrl")
                or item.get("seoUrl")
                or item.get("url")
                or item.get("jdURL")
                or ""
            )
            job_id = str(item.get("jobId") or item.get("id") or "").strip() or None
            if raw_url and not str(raw_url).startswith("http"):
                raw_url = urljoin(BASE_URL, str(raw_url))
            if not raw_url and job_id:
                raw_url = f"{BASE_URL}/job/{job_id}"
            if not raw_url:
                continue

            locations = item.get("locations") or item.get("jobLocation") or item.get("location")
            if isinstance(locations, list):
                location = ", ".join(
                    str(x.get("name") if isinstance(x, dict) else x) for x in locations if x
                )
            else:
                location = str(locations).strip() if locations else None

            jobs.append(
                ScrapedJob(
                    source=self.source,
                    external_id=job_id,
                    title=title,
                    company=company,
                    url=str(raw_url).split("?")[0],
                    location=location,
                    description=item.get("description") or item.get("jobDescription"),
                    employment_type=item.get("employmentType") or item.get("jobType"),
                    currency="INR",
                    raw_payload={"via": "foundit-api", "skills": item.get("skills")},
                )
            )
        return jobs

    async def _jobs_from_dom(self) -> list[ScrapedJob]:
        selectors = (
            "div.cardContainer",
            "div.srpResultCard",
            "article.job-card",
            "div[class*='jobCard']",
            "div.card-apply-content",
        )
        for sel in selectors:
            cards = self.page.locator(sel)
            count = await cards.count()
            if count == 0:
                continue
            jobs: list[ScrapedJob] = []
            for i in range(count):
                card = cards.nth(i)
                link = card.locator("a[href*='/job/'], a[href*='/jobs/']").first
                if await link.count() == 0:
                    continue
                title = (await link.inner_text()).strip()
                href = await link.get_attribute("href")
                if not title or not href:
                    continue
                url = href if href.startswith("http") else urljoin(BASE_URL, href)
                company = "Unknown"
                company_el = card.locator(
                    "[class*='company'], [class*='Company'], span.company-name"
                ).first
                if await company_el.count():
                    company = (await company_el.inner_text()).strip() or company
                external_id = None
                m = re.search(r"/job/(?:[^/]*-)?(\d+)", url)
                if m:
                    external_id = m.group(1)
                jobs.append(
                    ScrapedJob(
                        source=self.source,
                        external_id=external_id,
                        title=title,
                        company=company,
                        url=url.split("?")[0],
                        currency="INR",
                        raw_payload={"via": "foundit-dom"},
                    )
                )
            if jobs:
                logger.info("foundit_dom selector=%s count=%s", sel, len(jobs))
                return jobs

        content = (await self.page.content()).lower()
        if any(x in content for x in ("access denied", "captcha", "akamai")):
            raise ScraperBlockedError("Foundit blocked the session")
        return []

    @staticmethod
    def _dedupe(jobs: list[ScrapedJob]) -> list[ScrapedJob]:
        seen: set[str] = set()
        out: list[ScrapedJob] = []
        for job in jobs:
            key = job.external_id or job.url
            if key in seen:
                continue
            seen.add(key)
            out.append(job)
        return out
