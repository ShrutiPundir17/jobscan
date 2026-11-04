from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from celery.exceptions import SoftTimeLimitExceeded

from app.celery_app import celery
from app.config import settings
from app.db import SessionLocal
from app.services import scan_guard
from app.services.job_dedupe import dedupe_scraped_jobs
from app.services.job_ingest import upsert_scraped_jobs

logger = logging.getLogger(__name__)

SOFT_TIME_LIMIT = 15 * 60
HARD_TIME_LIMIT = 18 * 60
# Stop starting new portal searches this long before the soft limit, so what
# was collected gets saved instead of being lost to SoftTimeLimitExceeded.
TIME_MARGIN = 150
MAX_ERRORS_REPORTED = 20


def _parse_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _normalize_locations(raw: list[str] | None) -> list[str | None]:
    if not raw:
        return [None]
    out: list[str] = []
    for loc in raw:
        text = str(loc).strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in {"bangalore", "bengaluru"}:
            out.extend(["bangalore", "bengaluru"])
        else:
            out.append(text)
    # unique, preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for loc in out:
        key = loc.lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(loc)
    return uniq or [None]


class ScannerConfigError(RuntimeError):
    pass


def scanner_proxy() -> dict[str, str] | None:
    """
    Playwright proxy settings from SCANNER_PROXY_URL. A malformed value fails
    the scan rather than silently scraping without the proxy the operator asked for.
    """
    raw = (settings.scanner_proxy_url or "").strip()
    if not raw:
        return None
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except ValueError as exc:
        raise ScannerConfigError(f"SCANNER_PROXY_URL is invalid: {scan_guard.sanitize(raw)}") from exc
    if parsed.scheme not in {"http", "https", "socks5"} or not parsed.hostname:
        raise ScannerConfigError(f"SCANNER_PROXY_URL is invalid: {scan_guard.sanitize(raw)}")
    proxy = {"server": f"{parsed.scheme}://{parsed.hostname}{f':{port}' if port else ''}"}
    if parsed.username:
        proxy["username"] = parsed.username
        proxy["password"] = parsed.password or ""
    return proxy


async def _scrape_portal(
    portal: str,
    *,
    keyword: str,
    location: str | None,
    max_pages: int,
    headless: bool,
    browser_channel: str | None,
    proxy: dict[str, str] | None = None,
) -> list[Any]:
    from job_scanner import (
        FounditScraper,
        InternshalaScraper,
        LinkedInScraper,
        NaukriScraper,
        UnstopScraper,
    )

    cls_map = {
        "naukri": NaukriScraper,
        "linkedin": LinkedInScraper,
        "internshala": InternshalaScraper,
        "foundit": FounditScraper,
        "unstop": UnstopScraper,
    }
    if portal not in cls_map:
        raise ValueError(f"Unknown portal: {portal}")
    cls = cls_map[portal]

    async with cls(
        keyword=keyword,
        location=location,
        max_pages=max_pages,
        headless=headless,
        browser_channel=browser_channel,
        proxy=proxy,
    ) as scraper:
        return await scraper.scrape()


def _ingest(jobs: list[Any]) -> dict[str, int]:
    db = SessionLocal()
    try:
        return upsert_scraped_jobs(db, jobs)
    finally:
        db.close()


def _stamp(jobs: list[Any], keyword: str, location: str | None) -> None:
    # Portals often omit location on cards — stamp the search city
    # so preferred-location matching actually works.
    for job in jobs:
        if location and not (getattr(job, "location", None) or "").strip():
            job.location = str(location).strip().title()
        raw = getattr(job, "raw_payload", None)
        if not isinstance(raw, dict):
            raw = {}
            job.raw_payload = raw
        if location:
            raw["search_location"] = str(location).strip()
        raw["search_keyword"] = keyword


def _backoff(attempt: int) -> float:
    base = max(0.0, settings.scanner_retry_base_seconds)
    return base * (2**attempt) * random.uniform(0.75, 1.25)


async def _run_scan(
    *,
    keywords: list[str] | None = None,
    locations: list[str] | None = None,
    scrape: Callable[..., Awaitable[list[Any]]] | None = None,
    ingest: Callable[[list[Any]], dict[str, int]] | None = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    deadline: float | None = None,
) -> dict[str, Any]:
    from job_scanner.exceptions import ScraperBlockedError

    scrape = scrape or _scrape_portal
    ingest = ingest or _ingest
    deadline = deadline if deadline is not None else time.monotonic() + SOFT_TIME_LIMIT - TIME_MARGIN

    kw = (keywords if keywords else _parse_csv(settings.scanner_keywords))[: max(1, settings.scanner_max_keywords)]
    locs = (
        _normalize_locations(locations)
        if locations is not None
        else (_parse_csv(settings.scanner_locations) or [None])
    )[: max(1, settings.scanner_max_locations)]
    portals = [p.lower() for p in _parse_csv(settings.scanner_portals)]
    max_pages = max(1, settings.scanner_max_pages)
    retries = max(0, settings.scanner_retries)

    summary: dict[str, Any] = {
        "portals": portals,
        "keywords": kw,
        "locations": [loc or "" for loc in locs],
        "scraped": 0,
        "scraped_raw": 0,
        "ingest": {"inserted": 0, "updated": 0, "skipped": 0},
        "errors": [],
        "by_portal": {},
        "truncated": False,
    }

    def error(msg: str) -> None:
        if len(summary["errors"]) < MAX_ERRORS_REPORTED:
            summary["errors"].append(scan_guard.sanitize(msg))

    if not settings.scanner_enabled:
        summary["status"] = "disabled"
        return summary
    if not kw:
        summary["status"] = "skipped"
        error("No scanner keywords configured")
        return summary
    try:
        proxy = scanner_proxy()
    except ScannerConfigError as exc:
        summary["status"] = "failed"
        error(str(exc))
        return summary

    headless = settings.scanner_headless
    channel = settings.scanner_browser_channel or None
    portal_outcomes: list[str] = []

    for portal in portals:
        report: dict[str, Any] = {"status": "ok", "scraped": 0, "errors": 0}
        summary["by_portal"][portal] = report
        until = scan_guard.portal_cooldown_until(portal)
        if until is not None:
            report.update(status="cooling_down", until=until.isoformat())
            portal_outcomes.append("cooling_down")
            continue

        portal_jobs: list[Any] = []
        succeeded = 0
        blocked: Exception | None = None
        last_error: Exception | None = None
        for keyword in kw:
            for location in locs:
                if blocked or summary["truncated"]:
                    break
                for attempt in range(retries + 1):
                    if time.monotonic() >= deadline:
                        summary["truncated"] = True
                        break
                    try:
                        jobs = await scrape(
                            portal,
                            keyword=keyword,
                            location=location,
                            max_pages=max_pages,
                            headless=headless,
                            browser_channel=channel,
                            proxy=proxy,
                        )
                    except SoftTimeLimitExceeded:
                        raise
                    except ScraperBlockedError as exc:
                        # Never retry a block: back off from the whole portal.
                        blocked = exc
                        error(f"{portal}/{keyword}/{location or '-'}: blocked: {exc}")
                        break
                    except Exception as exc:  # noqa: BLE001 — timeouts, navigation, parsing
                        last_error = exc
                        if attempt < retries:
                            logger.warning(
                                "scan_retry portal=%s keyword=%s location=%s attempt=%s err=%s",
                                portal, keyword, location, attempt + 1, scan_guard.sanitize(exc),
                            )
                            await sleep(_backoff(attempt))
                            continue
                        report["errors"] += 1
                        error(f"{portal}/{keyword}/{location or '-'}: {exc}")
                        logger.error("scan_failed portal=%s keyword=%s location=%s err=%s",
                                     portal, keyword, location, scan_guard.sanitize(exc))
                        break
                    _stamp(jobs, keyword, location)
                    portal_jobs.extend(jobs)
                    succeeded += 1
                    logger.info("scan_ok portal=%s keyword=%s location=%s count=%s",
                                portal, keyword, location, len(jobs))
                    break
            if blocked or summary["truncated"]:
                break

        # Save this portal's jobs now: a later timeout must not discard them.
        save_failed = False
        if portal_jobs:
            summary["scraped_raw"] += len(portal_jobs)
            unique = dedupe_scraped_jobs(portal_jobs)
            report["scraped"] = len(unique)
            summary["scraped"] += len(unique)
            try:
                counts = ingest(unique)
                for k in ("inserted", "updated", "skipped"):
                    summary["ingest"][k] += int(counts.get(k, 0))
            except Exception as exc:  # noqa: BLE001
                save_failed = True
                error(f"{portal}: saving jobs failed: {exc}")
                logger.exception("scan_ingest_failed portal=%s", portal)

        if blocked is not None:
            cooldown = scan_guard.record_portal_blocked(portal, blocked)
            report.update(status="blocked", until=cooldown.isoformat() if cooldown else None)
            portal_outcomes.append("blocked")
        elif succeeded:
            # The portal itself worked even if our DB write didn't — keep its health clean.
            scan_guard.record_portal_success(portal, report["scraped"])
            report["status"] = "save_failed" if save_failed else ("partial" if report["errors"] else "ok")
            portal_outcomes.append(report["status"])
        elif last_error is not None:
            cooldown = scan_guard.record_portal_error(portal, last_error)
            report.update(status="failed", until=cooldown.isoformat() if cooldown else None)
            portal_outcomes.append("failed")
        else:
            report["status"] = "not_started"  # ran out of time before reaching it
            portal_outcomes.append("not_started")
        if summary["truncated"]:
            for later in portals[portals.index(portal) + 1:]:
                summary["by_portal"][later] = {"status": "not_started", "scraped": 0, "errors": 0}
            break

    if portal_outcomes and all(o == "ok" for o in portal_outcomes) and not summary["truncated"]:
        summary["status"] = "ok"
    elif summary["scraped"] > 0:
        summary["status"] = "partial"
    elif portal_outcomes and all(o in {"blocked", "cooling_down"} for o in portal_outcomes):
        summary["status"] = "blocked"
    else:
        summary["status"] = "failed" if summary["errors"] or summary["truncated"] else "partial"
    return summary


@celery.task(
    name="app.tasks.job_scanner.scan_jobs",
    soft_time_limit=SOFT_TIME_LIMIT,
    time_limit=HARD_TIME_LIMIT,
)
def scan_jobs(
    keywords: list[str] | None = None,
    locations: list[str] | None = None,
) -> dict[str, Any]:
    """Celery entrypoint — scrape portals (optional per-user keywords/locations)."""
    from app.services.scan_status import record_scan_event

    # Outlives the hard limit so a killed worker can't wedge scanning forever.
    token = scan_guard.acquire_scan_lock(timedelta(seconds=HARD_TIME_LIMIT + 60))
    if token is None:
        logger.info("scan_jobs_skipped reason=another scan is running")
        return {"status": "skipped", "reason": "another scan is running", "scraped": 0}
    scan_guard.clear_queued()

    logger.info("scan_jobs_started keywords=%s locations=%s", keywords, locations)
    record_scan_event(status="running", scraped=0, bump_daily=False)
    result: dict[str, Any] = {"status": "failed", "scraped": 0, "errors": []}
    try:
        result = asyncio.run(_run_scan(keywords=keywords, locations=locations))
        logger.info(
            "scan_jobs_finished status=%s scraped=%s ingest=%s errors=%s",
            result.get("status"),
            result.get("scraped"),
            result.get("ingest"),
            len(result.get("errors") or []),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("scan_jobs_crashed err=%s", scan_guard.sanitize(exc))
        result = {
            "status": "failed",
            "scraped": 0,
            "errors": [scan_guard.sanitize(exc)],
            "ingest": {"inserted": 0, "updated": 0, "skipped": 0},
        }
        raise
    finally:
        record_scan_event(
            status=str(result.get("status") or "failed"),
            scraped=int(result.get("scraped") or 0),
            bump_daily=True,
        )
        scan_guard.release_scan_lock(token)

    try:
        from app.tasks.job_embeddings import embed_pending_jobs

        embed_pending_jobs.delay()
        result["embed_queued"] = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("embed_queue_failed err=%s", exc)
        result["embed_queued"] = False
    return result
