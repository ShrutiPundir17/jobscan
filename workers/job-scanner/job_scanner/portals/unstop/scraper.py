from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode, urljoin

from job_scanner.base import BaseScraper
from job_scanner.exceptions import ScraperBlockedError
from job_scanner.models import ScrapedJob

logger = logging.getLogger(__name__)

BASE_URL = "https://unstop.com"
API_PATH = "/api/public/opportunity/search-result"


def build_api_url(
    keyword: str,
    *,
    opportunity: str = "jobs",
    page: int = 1,
    per_page: int = 20,
) -> str:
    params = {
        "opportunity": opportunity,
        "page": str(max(1, page)),
        "per_page": str(per_page),
        "searchTerm": keyword.strip(),
    }
    return f"{BASE_URL}{API_PATH}?{urlencode(params)}"


class UnstopScraper(BaseScraper):
    """
    Unstop opportunities scraper (jobs + internships via public API).

    Uses Unstop's public search-result endpoint (no login).
    """

    name = "unstop"
    source = "unstop"

    def __init__(
        self,
        *,
        keyword: str,
        location: str | None = None,
        max_pages: int = 1,
        per_page: int = 20,
        include_internships: bool = True,
        headless: bool = False,
        browser_channel: str | None = "chrome",
        **kwargs: Any,
    ) -> None:
        super().__init__(headless=headless, browser_channel=browser_channel, **kwargs)
        self.keyword = keyword.strip()
        self.location = location.strip().lower() if location else None
        self.max_pages = max(1, max_pages)
        self.per_page = max(1, min(per_page, 50))
        self.include_internships = include_internships
        self.search_url = build_api_url(self.keyword, opportunity="jobs")

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
            "extra_http_headers": {
                "Accept": "application/json",
                "Accept-Language": "en-IN,en;q=0.9",
            },
        }

    async def scrape(self) -> list[ScrapedJob]:
        await self.goto(BASE_URL + "/")
        await self.human_pause("navigate")

        kinds = ["jobs"]
        if self.include_internships:
            kinds.append("internships")

        jobs: list[ScrapedJob] = []
        for kind in kinds:
            for page_num in range(1, self.max_pages + 1):
                api_url = build_api_url(
                    self.keyword,
                    opportunity=kind,
                    page=page_num,
                    per_page=self.per_page,
                )
                logger.info("unstop_page kind=%s page=%s", kind, page_num)
                items = await self._fetch_page(api_url)
                mapped = self._map_items(items, kind=kind)
                jobs.extend(mapped)
                if len(items) < self.per_page:
                    break
                await self.human_pause("page")

        deduped = self._dedupe(jobs)
        logger.info("unstop_done count=%s", len(deduped))
        return deduped

    @staticmethod
    def _extract_items(payload: Any) -> list[dict[str, Any]]:
        """
        Unstop wraps listings as:
          { data: { current_page, data: [ ...jobs ], total, ... } }
        Older shapes may use a top-level list under data.
        """
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            nested = data.get("data")
            if isinstance(nested, list):
                return [x for x in nested if isinstance(x, dict)]
        return []

    async def _fetch_page(self, api_url: str) -> list[dict[str, Any]]:
        # Prefer in-page fetch (same-origin cookies/session); fall back to request API.
        try:
            data = await self.page.evaluate(
                """async (url) => {
                  const res = await fetch(url, {
                    headers: { 'Accept': 'application/json' },
                    credentials: 'include',
                  });
                  if (!res.ok) return { __status: res.status, data: [] };
                  const json = await res.json();
                  return { __status: res.status, ...json };
                }""",
                api_url,
            )
            status = int((data or {}).get("__status") or 0)
            if status in {401, 403, 429}:
                raise ScraperBlockedError(f"Unstop blocked API ({status})")
            items = self._extract_items(data)
            if items:
                return items
        except ScraperBlockedError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("unstop_fetch_eval_failed err=%s", exc)

        response = await self.page.request.get(
            api_url,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": f"{BASE_URL}/",
                "Origin": BASE_URL,
            },
        )
        if response.status in {401, 403, 429}:
            raise ScraperBlockedError(f"Unstop blocked API ({response.status})")
        if response.status != 200:
            logger.warning("unstop_bad_status status=%s", response.status)
            return []
        data = await response.json()
        items = self._extract_items(data)
        if not items:
            logger.warning(
                "unstop_unexpected_payload type=%s",
                type((data or {}).get("data") if isinstance(data, dict) else data),
            )
        return items

    def _map_items(self, items: list[dict[str, Any]], *, kind: str) -> list[ScrapedJob]:
        jobs: list[ScrapedJob] = []
        for item in items:
            title = (item.get("title") or "").strip()
            company = self._company_from_item(item)
            if not title:
                continue

            public_url = item.get("public_url") or item.get("seo_url") or ""
            if public_url and not str(public_url).startswith("http"):
                public_url = urljoin(BASE_URL, str(public_url))
            external_id = str(item.get("id") or item.get("short_id") or "").strip() or None
            if not public_url and external_id:
                public_url = f"{BASE_URL}/j/{external_id}"
            if not public_url:
                continue

            details = item.get("details")
            job_detail = item.get("jobDetail") if isinstance(item.get("jobDetail"), dict) else {}
            location = self._location_from_item(item)
            description = details if isinstance(details, str) and details.strip() else None
            if not description and isinstance(job_detail, dict):
                description = job_detail.get("description") or job_detail.get("jobDescription")

            if self.location and location and self.location not in location.lower():
                # Soft filter — keep remote / pan-India listings
                if "remote" not in location.lower() and "india" not in location.lower():
                    continue

            posted_at = None
            for key in ("updated_at", "start_date", "created_at", "approved_date"):
                raw = item.get(key)
                if not raw and isinstance(job_detail, dict):
                    raw = job_detail.get(key)
                if not raw:
                    continue
                try:
                    posted_at = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                    if posted_at.tzinfo is None:
                        posted_at = posted_at.replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue

            jobs.append(
                ScrapedJob(
                    source=self.source,
                    external_id=external_id,
                    title=title,
                    company=company,
                    url=str(public_url).split("?")[0],
                    location=location,
                    description=description,
                    employment_type=kind.rstrip("s") if kind.endswith("s") else kind,
                    currency="INR",
                    posted_at=posted_at,
                    raw_payload={
                        "type": item.get("type"),
                        "subtype": item.get("subtype"),
                        "via": "unstop-api",
                    },
                )
            )
        return jobs

    @staticmethod
    def _location_from_item(item: dict[str, Any]) -> str | None:
        loc_fields = item.get("locations") or item.get("region") or item.get("location")
        if isinstance(loc_fields, list):
            parts: list[str] = []
            for x in loc_fields:
                if isinstance(x, dict):
                    name = x.get("location") or x.get("name") or x.get("city")
                    if name:
                        parts.append(str(name))
                elif x:
                    parts.append(str(x))
            return ", ".join(parts) if parts else None
        if isinstance(loc_fields, dict):
            name = loc_fields.get("location") or loc_fields.get("name")
            return str(name) if name else None
        if loc_fields:
            return str(loc_fields)
        return None

    @staticmethod
    def _company_from_item(item: dict[str, Any]) -> str:
        for key in ("organisation", "organization", "company", "brand"):
            raw = item.get(key)
            if isinstance(raw, dict):
                name = raw.get("name") or raw.get("title") or raw.get("company_name")
                if name and str(name).strip():
                    return str(name).strip()
            elif isinstance(raw, str) and raw.strip():
                return raw.strip()
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        for key in ("organisation_name", "organization_name", "company_name", "company"):
            val = details.get(key)
            if val and str(val).strip():
                return str(val).strip()
        return "Unknown"

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
