from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urljoin

from job_scanner.base import BaseScraper
from job_scanner.exceptions import ScraperBlockedError
from job_scanner.models import ScrapedJob

logger = logging.getLogger(__name__)

BASE_URL = "https://internshala.com"


def slugify(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return re.sub(r"-{2,}", "-", text).strip("-")


def build_search_url(
    keyword: str,
    location: str | None = None,
    *,
    kind: str = "jobs",
    page: int = 1,
) -> str:
    """
    kind: 'jobs' | 'internships'
    Page 1: /jobs/keywords-python/
    Page 2: /jobs/keywords-python/page-2/
    """
    path_root = "jobs" if kind == "jobs" else "internships"
    kw = slugify(keyword) if keyword else ""
    loc = slugify(location) if location else ""

    if kw and loc:
        base = f"{BASE_URL}/{path_root}/keywords-{kw}-in-{loc}/"
    elif kw:
        base = f"{BASE_URL}/{path_root}/keywords-{kw}/"
    elif loc:
        base = f"{BASE_URL}/{path_root}/{loc}-jobs/" if kind == "jobs" else f"{BASE_URL}/{path_root}/{loc}-internship/"
    else:
        base = f"{BASE_URL}/{path_root}/"

    if page > 1:
        return f"{base.rstrip('/')}/page-{page}/"
    return base


class InternshalaScraper(BaseScraper):
    """Internshala jobs + internships scraper (public listing pages)."""

    name = "internshala"
    source = "internshala"

    def __init__(
        self,
        *,
        keyword: str,
        location: str | None = None,
        max_pages: int = 1,
        include_internships: bool = True,
        headless: bool = False,
        browser_channel: str | None = "chrome",
        **kwargs: Any,
    ) -> None:
        super().__init__(headless=headless, browser_channel=browser_channel, **kwargs)
        self.keyword = keyword.strip()
        self.location = location.strip() if location else None
        self.max_pages = max(1, max_pages)
        self.include_internships = include_internships
        self.search_url = build_search_url(self.keyword, self.location, kind="jobs")

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
        kinds = ["jobs"]
        if self.include_internships:
            kinds.append("internships")

        jobs: list[ScrapedJob] = []
        for kind in kinds:
            for page_num in range(1, self.max_pages + 1):
                url = build_search_url(
                    self.keyword, self.location, kind=kind, page=page_num
                )
                logger.info("internshala_page kind=%s page=%s url=%s", kind, page_num, url)
                await self.goto(url)
                await self._dismiss_overlays()
                page_jobs = await self._extract_cards(kind=kind)
                jobs.extend(page_jobs)
                if not page_jobs:
                    break
                await self.human_pause("page")

        deduped = self._dedupe(jobs)
        logger.info("internshala_done count=%s", len(deduped))
        return deduped

    async def _dismiss_overlays(self) -> None:
        for sel in (
            "#close_popup",
            "button.close",
            ".modal-close",
            'button:has-text("Accept")',
            'button:has-text("Got it")',
        ):
            try:
                loc = self.page.locator(sel).first
                if await loc.count() and await loc.is_visible():
                    await loc.click(timeout=1200)
                    await self.human_pause("action")
            except Exception:  # noqa: BLE001
                continue

    async def _extract_cards(self, *, kind: str) -> list[ScrapedJob]:
        cards = self.page.locator(
            "div.individual_internship, div[class*='individual_internship'], div.internship_list_container > div"
        )
        try:
            await self.page.wait_for_selector(
                "div.individual_internship, h3.heading_4_5, div.company",
                timeout=12_000,
            )
        except Exception:  # noqa: BLE001
            content = (await self.page.content()).lower()
            if "captcha" in content or "access denied" in content:
                raise ScraperBlockedError("Internshala blocked the session")
            logger.warning("internshala_no_cards kind=%s", kind)

        count = await cards.count()
        if count == 0:
            cards = self.page.locator("div.individual_internship")
            count = await cards.count()

        jobs: list[ScrapedJob] = []
        for i in range(count):
            card = cards.nth(i)
            title_el = card.locator(
                "h3.heading_4_5 a, a.job-title-href, div.heading_4_5 a"
            ).first
            if await title_el.count() == 0:
                title_el = card.locator(
                    "a[href*='/job/detail/'], a[href*='/internship/detail/']"
                ).first
            if await title_el.count() == 0:
                continue
            title = (await title_el.inner_text()).strip()
            href = await title_el.get_attribute("href")
            if not title or not href:
                continue
            url = href if href.startswith("http") else urljoin(BASE_URL, href)

            company = "Unknown"
            company_el = card.locator(
                "p.company-name, a.company-name, div.company_name a, "
                "div.company_and_premium a, .company_name, "
                "a.link_display_like_text"
            ).first
            if await company_el.count():
                company_text = (await company_el.inner_text()).strip()
                company_text = re.sub(
                    r"\s*(Actively hiring|Actively Hiring)\s*",
                    " ",
                    company_text,
                    flags=re.I,
                ).strip()
                company_text = re.sub(r"\s+", " ", company_text)
                if company_text and company_text.lower() != title.lower():
                    company = company_text

            # Internshala often puts company in a data attribute
            if company == "Unknown":
                for attr in ("data-company-name", "company_name"):
                    attr_val = await card.get_attribute(attr)
                    if attr_val and attr_val.strip():
                        company = attr_val.strip()
                        break

            location = None
            loc_el = card.locator(
                "a.location_link span, a.location_link, "
                "div.row-1-item.locations span, span.location, div.location"
            ).first
            if await loc_el.count():
                location = (await loc_el.inner_text()).strip() or None

            external_id = None
            m = re.search(r"-(\d+)(?:/)?$", url.split("?")[0])
            if m:
                external_id = m.group(1)

            employment_type = "internship" if "internship" in url or kind == "internships" else "job"

            jobs.append(
                ScrapedJob(
                    source=self.source,
                    external_id=external_id,
                    title=title,
                    company=company,
                    url=url.split("?")[0],
                    location=location,
                    employment_type=employment_type,
                    currency="INR",
                    raw_payload={"kind": kind, "via": "internshala-dom"},
                )
            )

        logger.info("internshala_parsed kind=%s count=%s", kind, len(jobs))
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
