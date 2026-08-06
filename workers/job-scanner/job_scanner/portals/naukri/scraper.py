from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

from job_scanner.base import BaseScraper
from job_scanner.exceptions import ScraperBlockedError
from job_scanner.models import ScrapedJob

logger = logging.getLogger(__name__)

BASE_URL = "https://www.naukri.com"
API_PATH = "jobapi/v3/search"

# Prefer API intercept; these are only used as a fallback if XHR is missed.
DOM_CARD_SELECTORS = (
    "article.jobTuple",
    "div.srp-jobtuple-wrapper",
    "div.cust-job-tuple",
)


def slugify(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return re.sub(r"-{2,}", "-", text).strip("-")


def build_search_url(keyword: str, location: str | None = None) -> str:
    """
    Build a Naukri SERP URL.

    Examples:
      python developer + bangalore -> /python-developer-jobs-in-bangalore
      python developer            -> /python-developer-jobs
      (empty) + pune              -> /jobs-in-pune
    """
    kw = slugify(keyword) if keyword else ""
    loc = slugify(location) if location else ""

    if kw and loc:
        return f"{BASE_URL}/{kw}-jobs-in-{loc}"
    if kw:
        return f"{BASE_URL}/{kw}-jobs"
    if loc:
        return f"{BASE_URL}/jobs-in-{loc}"
    return f"{BASE_URL}/jobs"


def paginated_url(base_search_url: str, page: int) -> str:
    """Page 1 is bare URL; page 2+ appends -{n}."""
    if page <= 1:
        return base_search_url
    return f"{base_search_url}-{page}"


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _parse_posted_at(raw: Any) -> datetime | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        # Naukri sometimes sends epoch millis
        ts = float(raw)
        if ts > 1_000_000_000_000:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(raw, str):
        text = raw.strip()
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ"):
            try:
                dt = datetime.strptime(text.replace("Z", ""), fmt.replace("Z", ""))
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


class NaukriScraper(BaseScraper):
    """
    Naukri.com search scraper.

    Strategy:
      1. Open public search pages with a stealth Chromium session
      2. Intercept Naukri's own `jobapi/v3/search` JSON (stable fields)
      3. Fall back to DOM card parsing if the API response is missed
    """

    name = "naukri"
    source = "naukri"

    def __init__(
        self,
        *,
        keyword: str,
        location: str | None = None,
        max_pages: int = 2,
        results_per_page: int = 20,
        # Akamai often blocks pure headless / bundled Chromium.
        headless: bool = False,
        browser_channel: str | None = "chrome",
        **kwargs: Any,
    ) -> None:
        super().__init__(headless=headless, browser_channel=browser_channel, **kwargs)
        self.keyword = keyword.strip()
        self.location = location.strip() if location else None
        self.max_pages = max(1, max_pages)
        self.results_per_page = results_per_page
        self.search_url = build_search_url(self.keyword, self.location)
        self._api_payloads: list[dict[str, Any]] = []
        self._seen_payload_keys: set[tuple[str, ...]] = set()

    def context_options(self) -> dict[str, Any]:
        # Stable Chrome profile — randomized UA / forced Referer headers break Naukri's SPA.
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
                "Accept-Language": "en-IN,en;q=0.9",
            },
        }

    async def scrape(self) -> list[ScrapedJob]:
        self._api_payloads.clear()
        self._seen_payload_keys.clear()
        self.page.on("response", lambda response: asyncio.create_task(self._capture_api(response)))

        # Warm cookies / JS challenge on the homepage before SERP (needed for Akamai)
        logger.info("naukri_warmup url=%s", BASE_URL + "/")
        await self.goto(BASE_URL + "/")
        await self.human_pause("navigate")

        for page_num in range(1, self.max_pages + 1):
            before = len(self._api_payloads)
            url = paginated_url(self.search_url, page_num)
            logger.info("naukri_page page=%s url=%s", page_num, url)
            await self._load_search_page(url)
            await self.scroll_down(900)
            await self.human_pause("page")

            new_payloads = self._api_payloads[before:]
            if new_payloads:
                jobs_on_page = new_payloads[-1].get("jobDetails") or []
                if len(jobs_on_page) < self.results_per_page:
                    logger.info("naukri_last_page page=%s count=%s", page_num, len(jobs_on_page))
                    break

        if self._api_payloads:
            items = [
                item
                for payload in self._api_payloads
                for item in (payload.get("jobDetails") or [])
            ]
            jobs = self._jobs_from_api(items)
            logger.info("naukri_done via=api count=%s", len(jobs))
            return self._dedupe(jobs)

        logger.warning("naukri_api_missed falling_back_to_dom")
        jobs = await self._jobs_from_dom()
        logger.info("naukri_done via=dom count=%s", len(jobs))
        return self._dedupe(jobs)

    async def _load_search_page(self, url: str) -> None:
        await self.human_pause("think")
        before = len(self._api_payloads)

        try:
            response = await self.page.goto(url, wait_until="domcontentloaded")
        except Exception as nav_exc:  # noqa: BLE001
            from job_scanner.exceptions import ScraperNavigationError

            raise ScraperNavigationError(f"Failed navigating to {url}: {nav_exc}") from nav_exc

        status = response.status if response else None
        await self._raise_if_blocked(status, url)

        # Wait until search API lands or cards render
        for _ in range(30):
            if len(self._api_payloads) > before:
                break
            try:
                if await self.page.locator(", ".join(DOM_CARD_SELECTORS)).count() > 0:
                    break
            except Exception:  # noqa: BLE001
                pass
            await self.page.wait_for_timeout(500)
        else:
            if len(self._api_payloads) <= before:
                logger.warning("naukri_no_cards_yet url=%s", url)

        await self.human_pause("navigate")

    async def _capture_api(self, response) -> None:
        try:
            if API_PATH not in response.url or response.status != 200:
                return
            data = await response.json()
            if not (isinstance(data, dict) and isinstance(data.get("jobDetails"), list)):
                return
            key = tuple(
                sorted(str(item.get("jobId") or item.get("id") or "") for item in data["jobDetails"])
            )
            if key in self._seen_payload_keys:
                return
            self._seen_payload_keys.add(key)
            self._api_payloads.append(data)
            logger.info(
                "naukri_api_hit jobs=%s total_hits=%s",
                len(data["jobDetails"]),
                len(self._api_payloads),
            )
        except Exception:  # noqa: BLE001
            logger.debug("naukri_api_parse_skip url=%s", getattr(response, "url", ""))

    def _jobs_from_api(self, items: list[dict[str, Any]]) -> list[ScrapedJob]:
        jobs: list[ScrapedJob] = []
        for item in items:
            job = self._map_api_item(item)
            if job is not None:
                jobs.append(job)
        return jobs

    def _map_api_item(self, item: dict[str, Any]) -> ScrapedJob | None:
        title = (item.get("title") or "").strip()
        company = (item.get("companyName") or item.get("companyId") or "").strip()
        if not title or not company:
            return None

        raw_url = item.get("jdURL") or item.get("jdUrl") or ""
        url = raw_url if str(raw_url).startswith("http") else urljoin(BASE_URL, str(raw_url))
        if not url or url == BASE_URL + "/":
            return None

        placeholders = item.get("placeholders") or []
        labels: list[str] = []
        location: str | None = None
        for ph in placeholders:
            if not isinstance(ph, dict):
                continue
            label = (ph.get("label") or "").strip()
            if not label:
                continue
            labels.append(label)
            ptype = str(ph.get("type") or ph.get("placeholderType") or "").lower()
            if "loc" in ptype:
                location = label

        # Typical card order: experience, salary, location
        if location is None and len(labels) >= 3:
            location = labels[2]
        elif location is None and labels:
            location = labels[-1]

        salary = item.get("salaryDetail") or {}
        salary_min = _as_int(salary.get("minimumSalary") or salary.get("min"))
        salary_max = _as_int(salary.get("maximumSalary") or salary.get("max"))
        currency = salary.get("currency") or ("INR" if salary_min or salary_max else None)

        external_id = str(item.get("jobId") or item.get("id") or "") or None
        description = item.get("jobDescription") or item.get("description")
        if isinstance(description, str):
            description = description.strip() or None

        posted_at = _parse_posted_at(
            item.get("createdDate")
            or item.get("createdDateTime")
            or item.get("postedDate")
        )

        employment_type = None
        for key in ("jobType", "employmentType", "workMode"):
            if item.get(key):
                employment_type = str(item[key]).strip()
                break

        return ScrapedJob(
            source=self.source,
            external_id=external_id,
            title=title,
            company=company if isinstance(company, str) else str(company),
            url=url,
            location=location,
            description=description,
            employment_type=employment_type,
            salary_min=salary_min,
            salary_max=salary_max,
            currency=currency,
            posted_at=posted_at,
            raw_payload={
                "footer": item.get("footerPlaceholderLabel"),
                "experienceText": labels[0] if labels else None,
                "salaryText": labels[1] if len(labels) > 1 else None,
                "skills": item.get("tagsAndSkills") or item.get("keySkills"),
                "companyId": item.get("companyId"),
            },
        )

    async def _jobs_from_dom(self) -> list[ScrapedJob]:
        jobs: list[ScrapedJob] = []
        for selector in DOM_CARD_SELECTORS:
            cards = self.page.locator(selector)
            count = await cards.count()
            if count == 0:
                continue
            logger.info("naukri_dom selector=%s count=%s", selector, count)
            for i in range(count):
                card = cards.nth(i)
                title_el = card.locator("a.title, a[class*='title']").first
                if await title_el.count() == 0:
                    continue
                title = (await title_el.inner_text()).strip()
                href = await title_el.get_attribute("href")
                if not title or not href:
                    continue
                url = href if href.startswith("http") else urljoin(BASE_URL, href)

                company = ""
                company_el = card.locator("a.comp-name, a[class*='comp-name'], span.comp-name").first
                if await company_el.count():
                    company = (await company_el.inner_text()).strip()

                location = None
                loc_el = card.locator("span.locWdth, span.loc, span[class*='loc']").first
                if await loc_el.count():
                    location = (await loc_el.inner_text()).strip()

                external_id = None
                m = re.search(r"-(\d+)(?:\?|$)", url)
                if m:
                    external_id = m.group(1)

                if not company:
                    company = "Unknown"

                jobs.append(
                    ScrapedJob(
                        source=self.source,
                        external_id=external_id,
                        title=title,
                        company=company,
                        url=url,
                        location=location,
                        currency=None,
                        raw_payload={"via": "dom", "selector": selector},
                    )
                )
            if jobs:
                break

        if not jobs:
            # Likely hard-blocked
            content = (await self.page.content()).lower()
            if any(m in content for m in ("access denied", "akamai", "bot detection", "captcha")):
                raise ScraperBlockedError("Naukri blocked the session (DOM empty + challenge markers)")

        return jobs

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
