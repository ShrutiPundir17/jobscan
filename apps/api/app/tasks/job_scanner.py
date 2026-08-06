from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.celery_app import celery
from app.config import settings
from app.db import SessionLocal
from app.services.job_dedupe import dedupe_scraped_jobs
from app.services.job_ingest import upsert_scraped_jobs

logger = logging.getLogger(__name__)


def _parse_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


async def _scrape_portal(
    portal: str,
    *,
    keyword: str,
    location: str | None,
    max_pages: int,
    headless: bool,
    browser_channel: str | None,
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
    ) as scraper:
        return await scraper.scrape()


async def _run_scan() -> dict[str, Any]:
    keywords = _parse_csv(settings.scanner_keywords)
    locations = _parse_csv(settings.scanner_locations) or [None]
    portals = [p.lower() for p in _parse_csv(settings.scanner_portals)]
    max_pages = max(1, settings.scanner_max_pages)
    headless = settings.scanner_headless
    channel = settings.scanner_browser_channel or None

    summary: dict[str, Any] = {
        "portals": portals,
        "keywords": keywords,
        "locations": [loc or "" for loc in locations],
        "scraped": 0,
        "ingest": {"inserted": 0, "updated": 0, "skipped": 0},
        "errors": [],
        "by_portal": {},
    }

    if not settings.scanner_enabled:
        summary["status"] = "disabled"
        return summary

    if not keywords:
        summary["status"] = "skipped"
        summary["errors"].append("No scanner keywords configured")
        return summary

    all_jobs: list[Any] = []

    for portal in portals:
        portal_count = 0
        for keyword in keywords:
            for location in locations:
                try:
                    jobs = await _scrape_portal(
                        portal,
                        keyword=keyword,
                        location=location,
                        max_pages=max_pages,
                        headless=headless,
                        browser_channel=channel,
                    )
                    all_jobs.extend(jobs)
                    portal_count += len(jobs)
                    logger.info(
                        "scan_ok portal=%s keyword=%s location=%s count=%s",
                        portal,
                        keyword,
                        location,
                        len(jobs),
                    )
                except Exception as exc:  # noqa: BLE001
                    msg = f"{portal}/{keyword}/{location or '-'}: {exc}"
                    logger.exception("scan_failed %s", msg)
                    summary["errors"].append(msg)
        summary["by_portal"][portal] = portal_count

    summary["scraped_raw"] = len(all_jobs)
    all_jobs = dedupe_scraped_jobs(all_jobs)
    summary["scraped"] = len(all_jobs)

    if all_jobs:
        db = SessionLocal()
        try:
            summary["ingest"] = upsert_scraped_jobs(db, all_jobs)
        finally:
            db.close()

    summary["status"] = "ok" if not summary["errors"] else "partial"
    return summary


@celery.task(
    name="app.tasks.job_scanner.scan_jobs",
    soft_time_limit=15 * 60,
    time_limit=18 * 60,
)
def scan_jobs() -> dict[str, Any]:
    """Celery entrypoint — scrape configured portals and upsert into jobs."""
    logger.info("scan_jobs_started")
    result = asyncio.run(_run_scan())
    logger.info(
        "scan_jobs_finished status=%s scraped=%s ingest=%s errors=%s",
        result.get("status"),
        result.get("scraped"),
        result.get("ingest"),
        len(result.get("errors") or []),
    )
    # Queue Stage 1 job embeddings for newly ingested / invalidated rows
    try:
        from app.tasks.job_embeddings import embed_pending_jobs

        embed_pending_jobs.delay()
        result["embed_queued"] = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("embed_queue_failed err=%s", exc)
        result["embed_queued"] = False
    return result
