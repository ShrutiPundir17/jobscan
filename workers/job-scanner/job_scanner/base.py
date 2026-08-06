from __future__ import annotations

import logging
import random
from abc import ABC, abstractmethod
from typing import Any

from job_scanner.delays import (
    DelayProfile,
    pause_after_navigation,
    pause_after_scroll,
    pause_between_actions,
    pause_between_pages,
    pause_thinking,
)
from job_scanner.exceptions import ScraperBlockedError, ScraperNavigationError
from job_scanner.models import ScrapedJob
from job_scanner.stealth import (
    apply_stealth,
    browser_context_options,
    browser_launch_args,
)

logger = logging.getLogger(__name__)


class BaseScraper(ABC):
    """
    Portal-agnostic scraper foundation.

    Subclasses implement `scrape()` for a specific job board.
    This base class owns:
    - Playwright browser lifecycle
    - Stealth context setup
    - Human-like delays between actions
    """

    name: str = "base"
    source: str = "unknown"

    def __init__(
        self,
        *,
        headless: bool = True,
        delay_profile: DelayProfile | None = None,
        navigation_timeout_ms: int = 45_000,
        browser_channel: str | None = None,
    ) -> None:
        self.headless = headless
        self.delays = delay_profile or DelayProfile()
        self.navigation_timeout_ms = navigation_timeout_ms
        # "chrome" / "msedge" use an installed browser (harder for Akamai to fingerprint)
        self.browser_channel = browser_channel
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

    async def __aenter__(self) -> BaseScraper:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    async def start(self) -> None:
        """Launch Chromium with stealth context."""
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "playwright is required for scraping. "
                "Install with: pip install playwright && playwright install chromium"
            ) from exc

        self._playwright = await async_playwright().start()
        self._browser = await self._launch_browser()
        self._context = await self._browser.new_context(**self.context_options())
        await apply_stealth(self._context)
        self._page = await self._context.new_page()
        self._page.set_default_timeout(self.navigation_timeout_ms)
        logger.info(
            "scraper_started name=%s source=%s headless=%s channel=%s",
            self.name,
            self.source,
            self.headless,
            self.browser_channel or "chromium",
        )

    async def _launch_browser(self):
        channels: list[str | None] = []
        if self.browser_channel:
            channels.append(self.browser_channel)
        # Prefer real Chrome when available — bundled Chromium is often blocked on Naukri
        for fallback in ("chrome", "msedge", None):
            if fallback not in channels:
                channels.append(fallback)

        last_error: Exception | None = None
        for channel in channels:
            try:
                browser = await self._playwright.chromium.launch(
                    **browser_launch_args(headless=self.headless, channel=channel)
                )
                self.browser_channel = channel
                return browser
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning("browser_launch_failed channel=%s err=%s", channel, exc)

        raise RuntimeError(f"Could not launch a browser for scraping: {last_error}")

    def context_options(self) -> dict[str, Any]:
        """Override in portal scrapers for locale / timezone / headers."""
        return browser_context_options()

    async def stop(self) -> None:
        """Close page/browser/playwright safely."""
        for closer, attr in (
            (getattr(self._context, "close", None), "_context"),
            (getattr(self._browser, "close", None), "_browser"),
            (getattr(self._playwright, "stop", None), "_playwright"),
        ):
            try:
                if closer is not None:
                    await closer()
            except Exception:  # noqa: BLE001
                logger.exception("failed_closing %s", attr)
            setattr(self, attr, None)
        self._page = None
        logger.info("scraper_stopped name=%s", self.name)

    @property
    def page(self):
        if self._page is None:
            raise RuntimeError("Scraper is not started. Use `async with scraper:` or call start().")
        return self._page

    async def human_pause(self, kind: str = "action") -> float:
        """Apply a named human delay profile."""
        mapping = {
            "action": pause_between_actions,
            "navigate": pause_after_navigation,
            "scroll": pause_after_scroll,
            "page": pause_between_pages,
            "think": pause_thinking,
        }
        fn = mapping.get(kind, pause_between_actions)
        waited = await fn(self.delays)
        logger.debug("human_pause kind=%s waited=%.2fs scraper=%s", kind, waited, self.name)
        return waited

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded") -> None:
        """Navigate like a person: pause, go, pause again."""
        await self.human_pause("think")
        try:
            response = await self.page.goto(url, wait_until=wait_until)
        except Exception as exc:  # noqa: BLE001
            raise ScraperNavigationError(f"Failed navigating to {url}: {exc}") from exc

        await self.human_pause("navigate")
        await self._raise_if_blocked(response.status if response else None, url)

    async def click(self, selector: str, *, timeout: int | None = None) -> None:
        await self.human_pause("action")
        await self.page.click(selector, timeout=timeout)
        await self.human_pause("action")

    async def type_text(self, selector: str, text: str, *, clear: bool = True) -> None:
        """Type with a small per-key delay instead of dumping the whole string."""
        await self.human_pause("action")
        locator = self.page.locator(selector)
        if clear:
            await locator.fill("")
        await locator.type(text, delay=random.randint(45, 140))
        await self.human_pause("action")

    async def scroll_down(self, pixels: int = 800) -> None:
        await self.page.evaluate("(y) => window.scrollBy(0, y)", pixels)
        await self.human_pause("scroll")

    async def _raise_if_blocked(self, status_code: int | None, url: str) -> None:
        if status_code in {401, 403, 429, 503}:
            raise ScraperBlockedError(f"Blocked ({status_code}) while opening {url}")

        content = ""
        try:
            content = (await self.page.content()).lower()
        except Exception:  # noqa: BLE001
            return

        markers = (
            "verify you are human",
            "unusual traffic",
            "access denied",
            "bot detection",
            "cf-challenge",
            "recaptcha required",
        )
        if any(marker in content for marker in markers):
            raise ScraperBlockedError(f"Anti-bot challenge detected on {url}")

    @abstractmethod
    async def scrape(self) -> list[ScrapedJob]:
        """Fetch and normalize jobs from this portal."""
