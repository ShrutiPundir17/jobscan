from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode, urljoin

from job_scanner.base import BaseScraper
from job_scanner.exceptions import ScraperBlockedError
from job_scanner.models import ScrapedJob

logger = logging.getLogger(__name__)

BASE_URL = "https://www.linkedin.com"
GUEST_SEARCH_PATH = "/jobs-guest/jobs/api/seeMoreJobPostings/search"
PUBLIC_SEARCH_PATH = "/jobs/search"
RESULTS_PER_PAGE = 25

CARD_SELECTORS = (
    "div.base-card",
    "div.base-search-card",
    "li.jobs-search-results__list-item",
)


def build_public_search_url(keyword: str, location: str | None = None) -> str:
    params: dict[str, str] = {}
    if keyword:
        params["keywords"] = keyword
    if location:
        params["location"] = location
    qs = urlencode(params)
    return f"{BASE_URL}{PUBLIC_SEARCH_PATH}?{qs}" if qs else f"{BASE_URL}{PUBLIC_SEARCH_PATH}"


def build_guest_search_url(
    keyword: str,
    location: str | None = None,
    *,
    start: int = 0,
) -> str:
    params: dict[str, str] = {
        "keywords": keyword,
        "start": str(max(0, start)),
        "trk": "public_jobs_jobs-search-bar_search-submit",
    }
    if location:
        params["location"] = location
    return f"{BASE_URL}{GUEST_SEARCH_PATH}?{urlencode(params)}"


def _parse_job_id(url: str | None, urn: str | None = None) -> str | None:
    if urn:
        m = re.search(r"(\d+)\s*$", urn)
        if m:
            return m.group(1)
    if not url:
        return None
    m = re.search(r"/jobs/view/(?:[^/?#]*-)?(\d+)(?:[/?#]|$)", url)
    if m:
        return m.group(1)
    m = re.search(r"currentJobId=(\d+)", url)
    if m:
        return m.group(1)
    return None


def _parse_posted_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = raw.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            dt = datetime.strptime(text.replace("Z", ""), fmt.replace("Z", ""))
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


class LinkedInScraper(BaseScraper):
    """
    LinkedIn public jobs scraper (no login).

    Strategy:
      1. Warm a Chrome session on the public jobs site
      2. Fetch LinkedIn guest HTML pages (`jobs-guest/.../seeMoreJobPostings`)
      3. Fall back to parsing the public `/jobs/search` DOM if guest API fails
    """

    name = "linkedin"
    source = "linkedin"

    def __init__(
        self,
        *,
        keyword: str,
        location: str | None = None,
        max_pages: int = 1,
        results_per_page: int = RESULTS_PER_PAGE,
        headless: bool = False,
        browser_channel: str | None = "chrome",
        **kwargs: Any,
    ) -> None:
        super().__init__(headless=headless, browser_channel=browser_channel, **kwargs)
        self.keyword = keyword.strip()
        self.location = location.strip() if location else None
        self.max_pages = max(1, max_pages)
        self.results_per_page = max(1, results_per_page)
        self.search_url = build_public_search_url(self.keyword, self.location)

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
                "Accept-Language": "en-IN,en;q=0.9",
            },
        }

    async def scrape(self) -> list[ScrapedJob]:
        logger.info("linkedin_warmup url=%s", self.search_url)
        await self.goto(self.search_url)
        await self._dismiss_overlays()
        await self.human_pause("navigate")

        jobs: list[ScrapedJob] = []
        for page_num in range(self.max_pages):
            start = page_num * self.results_per_page
            guest_url = build_guest_search_url(self.keyword, self.location, start=start)
            logger.info("linkedin_guest_page page=%s start=%s", page_num + 1, start)

            page_jobs = await self._scrape_guest_page(guest_url)
            if not page_jobs and page_num == 0:
                logger.warning("linkedin_guest_empty falling_back_to_dom")
                page_jobs = await self._scrape_dom()

            if not page_jobs:
                logger.info("linkedin_stop_empty page=%s", page_num + 1)
                break

            jobs.extend(page_jobs)
            if len(page_jobs) < self.results_per_page:
                break
            await self.human_pause("page")

        deduped = self._dedupe(jobs)
        logger.info("linkedin_done count=%s", len(deduped))
        return deduped

    async def _dismiss_overlays(self) -> None:
        """Best-effort dismiss cookie / sign-in modals on public pages."""
        selectors = (
            'button[action-type="ACCEPT"]',
            'button[data-test-modal-close-btn]',
            'button.artdeco-modal__dismiss',
            'button:has-text("Accept")',
            'button:has-text("Dismiss")',
            'button:has-text("Not now")',
            'button:has-text("No thanks")',
        )
        for sel in selectors:
            try:
                loc = self.page.locator(sel).first
                if await loc.count() and await loc.is_visible():
                    await loc.click(timeout=1500)
                    await self.human_pause("action")
            except Exception:  # noqa: BLE001
                continue

    async def _scrape_guest_page(self, guest_url: str) -> list[ScrapedJob]:
        try:
            response = await self.page.request.get(
                guest_url,
                headers={
                    "Accept": "*/*",
                    "Referer": self.search_url,
                    "Sec-Fetch-Dest": "empty",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Site": "same-origin",
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("linkedin_guest_request_failed err=%s", exc)
            return []

        status = response.status
        body = await response.text()
        logger.info("linkedin_guest_status status=%s bytes=%s", status, len(body))

        if status in {401, 403, 429, 999}:
            raise ScraperBlockedError(f"LinkedIn blocked guest search ({status})")
        if status != 200 or not body.strip():
            return []

        return await self._parse_cards_html(body)

    async def _parse_cards_html(self, html: str) -> list[ScrapedJob]:
        """
        Parse guest HTML fragment in-memory (no extra browser tab).

        The guest endpoint returns card markup without LinkedIn CSS — parsing it
        via a visible `set_content` tab looked like a broken plain page.
        """
        raw_jobs = await self.page.evaluate(
            """(html) => {
              const doc = new DOMParser().parseFromString(html, 'text/html');
              let cards = Array.from(doc.querySelectorAll('div.base-card, div.base-search-card'));
              if (!cards.length) {
                cards = Array.from(doc.querySelectorAll('li')).filter(
                  (li) => li.querySelector('h3.base-search-card__title, h3.base-card__title')
                );
              }
              const text = (el) => (el && el.textContent ? el.textContent.trim() : '');
              return cards.map((card) => {
                const titleEl = card.querySelector('h3.base-search-card__title, h3.base-card__title');
                const title = text(titleEl);
                if (!title) return null;
                const companyEl = card.querySelector(
                  'h4.base-search-card__subtitle, h4.base-card__subtitle, a.hidden-nested-link'
                );
                const locEl = card.querySelector(
                  'span.job-search-card__location, span.job-search-card__location--new'
                );
                const linkEl =
                  card.querySelector('a.base-card__full-link, a.base-card--link, a[href*="/jobs/view/"]') ||
                  card.querySelector("a[href*='jobs']");
                const timeEl = card.querySelector('time');
                return {
                  title,
                  company: text(companyEl) || 'Unknown',
                  location: text(locEl) || null,
                  href: linkEl ? linkEl.getAttribute('href') : null,
                  urn: card.getAttribute('data-entity-urn'),
                  posted: timeEl ? timeEl.getAttribute('datetime') : null,
                };
              }).filter(Boolean);
            }""",
            html,
        )

        jobs: list[ScrapedJob] = []
        for item in raw_jobs or []:
            href = item.get("href")
            urn = item.get("urn")
            if href and href.startswith("/"):
                href = urljoin(BASE_URL, href)
            if href:
                href = href.split("?")[0]
            external_id = _parse_job_id(href, urn)
            if not href and external_id:
                href = f"{BASE_URL}/jobs/view/{external_id}"
            if not href:
                continue
            jobs.append(
                ScrapedJob(
                    source=self.source,
                    external_id=external_id,
                    title=str(item.get("title") or "").strip(),
                    company=str(item.get("company") or "Unknown").strip(),
                    url=href,
                    location=(str(item["location"]).strip() if item.get("location") else None),
                    posted_at=_parse_posted_at(item.get("posted")),
                    currency=None,
                    raw_payload={"urn": urn, "via": "linkedin-guest"},
                )
            )

        logger.info("linkedin_parsed count=%s", len(jobs))
        return jobs

    async def _scrape_dom(self) -> list[ScrapedJob]:
        await self._dismiss_overlays()
        # Public list often lazy-loads — scroll a few times
        for _ in range(3):
            await self.scroll_down(1000)
        try:
            await self.page.wait_for_selector(", ".join(CARD_SELECTORS), timeout=12_000)
        except Exception:  # noqa: BLE001
            content = (await self.page.content()).lower()
            if any(m in content for m in ("authwall", "sign in", "challenge", "unusual activity")):
                raise ScraperBlockedError("LinkedIn showed an auth/challenge wall")
            logger.warning("linkedin_dom_no_cards url=%s", self.page.url)
            return []
        return await self._extract_jobs_from_page(self.page)

    async def _extract_jobs_from_page(self, page) -> list[ScrapedJob]:
        jobs: list[ScrapedJob] = []
        cards = page.locator("div.base-card, div.base-search-card")
        count = await cards.count()
        if count == 0:
            # Guest fragments sometimes wrap each card in <li>
            cards = page.locator("li")
            count = await cards.count()

        for i in range(count):
            card = cards.nth(i)
            title_el = card.locator("h3.base-search-card__title, h3.base-card__title").first
            if await title_el.count() == 0:
                continue
            title = (await title_el.inner_text()).strip()
            if not title:
                continue

            company = "Unknown"
            company_el = card.locator(
                "h4.base-search-card__subtitle, h4.base-card__subtitle, a.hidden-nested-link"
            ).first
            if await company_el.count():
                company = (await company_el.inner_text()).strip() or company

            location = None
            loc_el = card.locator("span.job-search-card__location, span.job-search-card__location--new").first
            if await loc_el.count():
                location = (await loc_el.inner_text()).strip() or None

            href = None
            link_el = card.locator(
                "a.base-card__full-link, a.base-card--link, a[href*='/jobs/view/']"
            ).first
            if await link_el.count():
                href = await link_el.get_attribute("href")
            if not href:
                any_link = card.locator("a[href*='jobs']").first
                if await any_link.count():
                    href = await any_link.get_attribute("href")

            urn = await card.get_attribute("data-entity-urn")
            if href and href.startswith("/"):
                href = urljoin(BASE_URL, href)
            if href:
                href = href.split("?")[0]

            external_id = _parse_job_id(href, urn)
            if not href and external_id:
                href = f"{BASE_URL}/jobs/view/{external_id}"
            if not href:
                continue

            posted_at = None
            time_el = card.locator("time").first
            if await time_el.count():
                posted_at = _parse_posted_at(await time_el.get_attribute("datetime"))

            jobs.append(
                ScrapedJob(
                    source=self.source,
                    external_id=external_id,
                    title=title,
                    company=company,
                    url=href,
                    location=location,
                    posted_at=posted_at,
                    currency=None,
                    raw_payload={"urn": urn, "via": "linkedin-guest-or-dom"},
                )
            )

        logger.info("linkedin_parsed count=%s", len(jobs))
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
