"""
Browser agent that fills public ATS application forms.

`prepare()` is a dry run: every non-GET request from the page is blocked and
the resume is never attached, so nothing reaches the employer. It reports
exactly what `submit()` would send.

`submit()` fills the form, refuses to continue if anything differs from the
approved plan, clicks a submit button it identified unambiguously, and only
reports success when *new* confirmation evidence appears. Once the click has
happened the result says so (`maybe_sent=True`), so callers never retry a
submission that may already have been received.
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
    INSPECT_JS,
    PAGE_STATE_JS,
    SUBMIT_JS,
    TITLE_JS,
    ApplicantProfile,
    classify_control,
    confirmation_hits,
    file_accepted,
    judge_after_submit,
    plan_fills,
    unanswered_required,
)

logger = logging.getLogger(__name__)

LABELS = {
    "first_name": "First name",
    "last_name": "Last name",
    "full_name": "Full name",
    "email": "Email",
    "email_confirm": "Confirm email",
    "phone": "Phone",
    "linkedin_url": "LinkedIn",
    "cover_letter": "Cover letter",
    "resume": "Resume",
}

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class ApplyAgentError(RuntimeError):
    """The form could not be opened or driven. Always raised before any submit click."""


@dataclass
class FormReport:
    provider: str
    url: str
    form_title: str = ""
    filled: list[dict[str, str]] = field(default_factory=list)
    unanswered_required: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    captcha: bool = False
    resume_attached: bool = False
    blocked_requests: int = 0

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
    # True when the employer may have received it (click fired, outcome unknown or
    # confirmed). Callers must never auto-retry such a submission.
    maybe_sent: bool = False
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
        confirm_timeout_s: float = 45.0,
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
            blocked = {"n": 0}

            async def block_writes(route):
                if route.request.method.upper() in _SAFE_METHODS:
                    await route.continue_()
                else:
                    blocked["n"] += 1
                    await route.abort()

            await page.route("**/*", block_writes)
            await self._open(page, url)
            report = await self._fill(page, url, provider, profile, dry_run=True)
            report.blocked_requests = blocked["n"]
            return report

    async def submit(
        self,
        url: str,
        provider: str,
        profile: ApplicantProfile,
        *,
        expected: dict[str, str] | None = None,
    ) -> SubmitResult:
        """
        `expected` is the {key: value} the user approved; if the form would now
        send anything different, abort before clicking submit.
        """
        async with self._session() as page:
            await self._open(page, url)
            report = await self._fill(page, url, provider, profile, dry_run=False)
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
                            "The application form changed since you approved it "
                            f"({', '.join(LABELS.get(k, k) for k in changed)}). Prepare again."
                        ),
                    )

            problem = await page.evaluate(SUBMIT_JS)
            if problem:
                return SubmitResult(False, report, error=problem)

            base_url = page.url
            base = await page.evaluate(PAGE_STATE_JS)
            base_hits = confirmation_hits(base.get("text", ""))
            await self._pause(0.6, 1.6)
            try:
                await page.locator("[data-ja-submit]").first.click()
            except Exception as exc:  # noqa: BLE001
                # A click that raised may still have fired; only call it safe if nothing moved.
                try:
                    state = await page.evaluate(PAGE_STATE_JS)
                    untouched = page.url == base_url and state.get("form_present")
                except Exception:  # noqa: BLE001
                    untouched = False
                if untouched:
                    return SubmitResult(False, report, error=f"Could not click submit: {exc}")
                return SubmitResult(False, report, maybe_sent=True, error=f"Submit click errored: {exc}")
            return await self._await_confirmation(page, report, base_url, base_hits)

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

    async def _fill(
        self,
        page,
        url: str,
        provider: str,
        profile: ApplicantProfile,
        *,
        dry_run: bool,
    ) -> FormReport:
        try:
            inspected = await page.evaluate(INSPECT_JS)
            title = await page.evaluate(TITLE_JS)
        except Exception as exc:  # noqa: BLE001
            raise ApplyAgentError(f"Could not read the application form: {exc}") from exc
        if not inspected.get("found"):
            raise ApplyAgentError("No application form found on the page.")
        controls = inspected["controls"]
        report = FormReport(provider=provider, url=url, form_title=title or "")

        resume_inputs = [c for c in controls if classify_control(c) == "resume"]
        if resume_inputs and profile.resume_path and not any(
            file_accepted(profile.resume_filename, c.get("accept")) for c in resume_inputs
        ):
            report.notes.append(
                f"The form does not accept '{profile.resume_filename}' "
                f"(allowed: {resume_inputs[0].get('accept')}). Re-upload your resume as PDF or DOCX."
            )

        for control, key, value in plan_fills(controls, profile):
            locator = page.locator(f'[data-ja-idx="{control["idx"]}"]').first
            try:
                if key == "resume":
                    if dry_run:
                        # Attaching can upload the file immediately — mark it instead.
                        await locator.evaluate("el => el.setAttribute('data-ja-planned', '1')")
                    else:
                        await locator.set_input_files(value)
                    report.resume_attached = True
                    shown = profile.resume_filename or "resume"
                else:
                    await locator.fill(value)
                    shown = value
            except Exception as exc:  # noqa: BLE001
                logger.warning("apply_agent_fill_failed key=%s err=%s", key, exc)
                continue
            report.filled.append({"key": key, "label": control.get("label") or LABELS[key], "value": shown})
            await self._pause(0.2, 0.7)

        try:
            # Re-inspect after filling: React forms may reveal or validate fields.
            after = await page.evaluate(INSPECT_JS)
            report.captcha = bool(await page.evaluate(CAPTCHA_JS))
        except Exception as exc:  # noqa: BLE001
            raise ApplyAgentError(f"The application form changed while filling: {exc}") from exc
        report.unanswered_required = unanswered_required(after.get("controls", []))
        return report

    async def _await_confirmation(self, page, report: FormReport, base_url: str, base_hits) -> SubmitResult:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.confirm_timeout_s
        last_rejection: str | None = None
        while loop.time() < deadline:
            await asyncio.sleep(0.5)
            try:
                state = await page.evaluate(PAGE_STATE_JS)
            except Exception:  # noqa: BLE001 — page mid-navigation
                continue
            verdict, detail = judge_after_submit(
                base_url=base_url, base_hits=base_hits, url=page.url, state=state
            )
            if verdict == "confirmed":
                return SubmitResult(True, report, maybe_sent=True, confirmation=detail)
            last_rejection = detail if verdict == "rejected" else None

        if last_rejection:
            # Same page, form still there, validation errors shown: it was not accepted.
            return SubmitResult(
                False, report, maybe_sent=False, error=f"The form rejected the submission: {last_rejection}"
            )
        try:
            if page.url == base_url and await page.evaluate(CAPTCHA_JS):
                return SubmitResult(False, report, maybe_sent=False, error="A captcha challenge blocked the submission.")
        except Exception:  # noqa: BLE001
            pass
        return SubmitResult(
            False,
            report,
            maybe_sent=True,
            error="Submit was clicked but no confirmation appeared — it may or may not have been received.",
        )

    async def _pause(self, low: float, high: float) -> None:
        if self.pace > 0:
            await asyncio.sleep(random.uniform(low, high) * self.pace)


def not_ready_reason(report: FormReport) -> str:
    parts: list[str] = list(report.notes)
    if report.captcha:
        parts.append("the form has a captcha")
    if not report.resume_attached and not report.notes:
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
