"""
Browser agent that fills public ATS application forms.

`prepare()` fills the form and reports what it would send — it never submits.
`submit()` fills again, re-checks that nothing required is missing, clicks
submit, and only reports success when the page shows a confirmation.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

from apply_agent.detect import ATS_HOSTS
from apply_agent.forms import (
    CAPTCHA_JS,
    CONFIRMATION_RE,
    CONFIRMATION_URL_RE,
    INSPECT_JS,
    PAGE_STATE_JS,
    SUBMIT_JS,
    ApplicantProfile,
    plan_fills,
    unanswered_required,
)

logger = logging.getLogger(__name__)

_LABELS = {
    "first_name": "First name",
    "last_name": "Last name",
    "full_name": "Full name",
    "email": "Email",
    "phone": "Phone",
    "linkedin_url": "LinkedIn",
    "cover_letter": "Cover letter",
    "resume": "Resume",
}


class ApplyAgentError(RuntimeError):
    """The form could not be opened or driven (navigation, off-ATS redirect, no form)."""


@dataclass
class FormReport:
    provider: str
    url: str
    filled: list[dict[str, str]] = field(default_factory=list)
    unanswered_required: list[str] = field(default_factory=list)
    captcha: bool = False
    resume_attached: bool = False

    @property
    def ready(self) -> bool:
        return not self.unanswered_required and not self.captcha and self.resume_attached

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ready"] = self.ready
        return data


@dataclass
class SubmitResult:
    submitted: bool
    report: FormReport
    confirmation: str | None = None
    error: str | None = None


class ApplyAgent:
    def __init__(
        self,
        *,
        headless: bool = True,
        browser_channel: str | None = None,
        allowed_hosts: set[str] | None = None,
        timeout_ms: int = 45_000,
        confirm_timeout_s: float = 25.0,
        pace: float = 1.0,
    ) -> None:
        self.headless = headless
        self.browser_channel = browser_channel or None
        self.allowed_hosts = set(allowed_hosts) if allowed_hosts is not None else set(ATS_HOSTS)
        self.timeout_ms = timeout_ms
        self.confirm_timeout_s = confirm_timeout_s
        # 0 disables human-like pauses (tests).
        self.pace = pace

    async def prepare(self, url: str, provider: str, profile: ApplicantProfile) -> FormReport:
        async with self._session() as page:
            await self._open(page, url)
            return await self._fill(page, url, provider, profile)

    async def submit(
        self,
        url: str,
        provider: str,
        profile: ApplicantProfile,
        *,
        expected: dict[str, str] | None = None,
    ) -> SubmitResult:
        """
        `expected` is the {key: value} the user approved at prepare time; if the
        form would now send anything different, abort before clicking submit.
        """
        async with self._session() as page:
            await self._open(page, url)
            report = await self._fill(page, url, provider, profile)
            if not report.ready:
                return SubmitResult(False, report, error=not_ready_reason(report))
            if expected is not None:
                actual = {f["key"]: f["value"] for f in report.filled}
                if actual != expected:
                    changed = sorted(k for k in set(actual) | set(expected) if actual.get(k) != expected.get(k))
                    return SubmitResult(
                        False,
                        report,
                        error=(
                            "The form or your profile changed since you approved it "
                            f"({', '.join(_LABELS.get(k, k) for k in changed)}). Prepare again."
                        ),
                    )

            if not await page.evaluate(SUBMIT_JS):
                return SubmitResult(False, report, error="Could not find the form's submit button.")
            await self._pause(0.6, 1.6)
            await page.locator("[data-ja-submit]").first.click()
            return await self._await_confirmation(page, report)

    # --- internals -------------------------------------------------------

    def _session(self):
        agent = self

        class _Session:
            async def __aenter__(self):
                from playwright.async_api import async_playwright

                self._pw = await async_playwright().start()
                launch: dict[str, Any] = {
                    "headless": agent.headless,
                    "args": ["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
                }
                if agent.browser_channel:
                    launch["channel"] = agent.browser_channel
                try:
                    self._browser = await self._pw.chromium.launch(**launch)
                except Exception:
                    await self._pw.stop()
                    raise
                self._context = await self._browser.new_context(**_context_options())
                try:
                    from job_scanner.stealth import apply_stealth

                    await apply_stealth(self._context)
                except ImportError:
                    pass
                page = await self._context.new_page()
                page.set_default_timeout(agent.timeout_ms)
                return page

            async def __aexit__(self, *exc):
                for closer in (self._context.close, self._browser.close, self._pw.stop):
                    try:
                        await closer()
                    except Exception:  # noqa: BLE001
                        logger.exception("apply_agent_close_failed")

        return _Session()

    def _check_host(self, url: str) -> None:
        host = (urlparse(url).hostname or "").lower()
        if host not in self.allowed_hosts:
            raise ApplyAgentError(f"Refusing to drive a form on non-ATS host '{host}'.")

    async def _open(self, page, url: str) -> None:
        self._check_host(url)
        try:
            response = await page.goto(url, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            raise ApplyAgentError(f"Could not open application form: {exc}") from exc
        if response is not None and response.status >= 400:
            raise ApplyAgentError(f"Application form returned HTTP {response.status} (job may be closed).")
        # Redirects (e.g. Greenhouse embed -> company careers site) must stay on an ATS host.
        self._check_host(page.url)
        try:
            await page.wait_for_selector("form input, form textarea", timeout=self.timeout_ms)
        except Exception as exc:  # noqa: BLE001
            raise ApplyAgentError("No application form found on the page (job may be closed).") from exc
        await self._pause(0.8, 2.0)

    async def _fill(self, page, url: str, provider: str, profile: ApplicantProfile) -> FormReport:
        inspected = await page.evaluate(INSPECT_JS)
        if not inspected.get("found"):
            raise ApplyAgentError("No application form found on the page.")
        controls = inspected["controls"]
        report = FormReport(provider=provider, url=url)

        for control, key, value in plan_fills(controls, profile):
            locator = page.locator(f'[data-ja-idx="{control["idx"]}"]').first
            try:
                if key == "resume":
                    await locator.set_input_files(value)
                    report.resume_attached = True
                    shown = profile.resume_filename or "resume"
                else:
                    await locator.fill(value)
                    shown = value
            except Exception as exc:  # noqa: BLE001
                logger.warning("apply_agent_fill_failed key=%s err=%s", key, exc)
                continue
            report.filled.append({"key": key, "label": control.get("label") or _LABELS[key], "value": shown})
            await self._pause(0.2, 0.7)

        # Re-inspect after filling: React forms may reveal or validate fields.
        after = await page.evaluate(INSPECT_JS)
        report.unanswered_required = unanswered_required(after.get("controls", []))
        report.captcha = bool(await page.evaluate(CAPTCHA_JS))
        return report

    async def _await_confirmation(self, page, report: FormReport) -> SubmitResult:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.confirm_timeout_s
        last_errors: list[str] = []
        while loop.time() < deadline:
            await asyncio.sleep(0.5)
            try:
                state = await page.evaluate(PAGE_STATE_JS)
            except Exception:  # noqa: BLE001 — page mid-navigation
                continue
            match = CONFIRMATION_RE.search(state.get("text", ""))
            if match or CONFIRMATION_URL_RE.search(page.url):
                return SubmitResult(True, report, confirmation=match.group(0) if match else page.url)
            try:
                if await page.evaluate(CAPTCHA_JS):
                    return SubmitResult(False, report, error="A captcha challenge appeared after submitting.")
            except Exception:  # noqa: BLE001
                pass
            last_errors = state.get("errors") or last_errors

        detail = f" Form errors: {'; '.join(last_errors)}" if last_errors else ""
        return SubmitResult(
            False,
            report,
            error=f"Submitted but no confirmation page was detected.{detail}",
        )

    async def _pause(self, low: float, high: float) -> None:
        if self.pace > 0:
            await asyncio.sleep(random.uniform(low, high) * self.pace)


def not_ready_reason(report: FormReport) -> str:
    parts: list[str] = []
    if report.captcha:
        parts.append("form has a captcha")
    if not report.resume_attached:
        parts.append("no resume upload field could be filled")
    if report.unanswered_required:
        parts.append("required questions need your answer: " + ", ".join(report.unanswered_required[:6]))
    return "Needs manual apply — " + "; ".join(parts) + "."


def _context_options() -> dict[str, Any]:
    try:
        from job_scanner.stealth import browser_context_options

        return browser_context_options()
    except ImportError:
        return {"viewport": {"width": 1366, "height": 900}, "locale": "en-US"}
