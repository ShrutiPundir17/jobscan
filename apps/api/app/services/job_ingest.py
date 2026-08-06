from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Job
from app.services.embeddings import clear_job_embedding
from app.services.job_dedupe import (
    dedupe_scraped_jobs,
    extract_external_id,
    normalize_job_url,
    url_fingerprint,
)

logger = logging.getLogger(__name__)


def upsert_scraped_jobs(db: Session, jobs: list[Any]) -> dict[str, int]:
    """
    Upsert scraped jobs with strong deduplication.

    - Dedupe within the batch
    - Match existing rows by (source, external_id) OR (source, url_fingerprint)
    - Unique constraints enforce no double-insert races
    """
    inserted = 0
    updated = 0
    skipped = 0
    before = len(jobs)
    unique_jobs = dedupe_scraped_jobs(jobs)
    duplicates_in_batch = max(0, before - len(unique_jobs))

    for job in unique_jobs:
        title = (getattr(job, "title", None) or "").strip()
        company = (getattr(job, "company", None) or "").strip()
        raw_url = (getattr(job, "url", None) or "").strip()
        source = (getattr(job, "source", None) or "").strip().lower()
        if not title or not company or not raw_url or not source:
            skipped += 1
            continue

        url = normalize_job_url(raw_url)
        external_id = extract_external_id(source, url, getattr(job, "external_id", None))
        fingerprint = url_fingerprint(url)

        payload = {
            "source": source,
            "external_id": external_id,
            "url_fingerprint": fingerprint,
            "title": title[:512],
            "company": company[:255],
            "url": url[:2048],
            "location": (getattr(job, "location", None) or None),
            "description": getattr(job, "description", None),
            "employment_type": getattr(job, "employment_type", None),
            "salary_min": getattr(job, "salary_min", None),
            "salary_max": getattr(job, "salary_max", None),
            "currency": getattr(job, "currency", None),
            "posted_at": getattr(job, "posted_at", None),
            "raw_payload": getattr(job, "raw_payload", None) or {},
        }

        existing = db.scalar(
            select(Job).where(
                Job.source == source,
                or_(
                    Job.external_id == external_id,
                    Job.url_fingerprint == fingerprint,
                ),
            )
        )

        if existing is not None:
            content_changed = (
                existing.title != payload["title"]
                or (payload["description"] and existing.description != payload["description"])
                or existing.company != payload["company"]
                or existing.employment_type != payload["employment_type"]
            )
            existing.external_id = external_id
            existing.url_fingerprint = fingerprint
            existing.title = payload["title"]
            existing.company = payload["company"]
            existing.url = payload["url"]
            # Never wipe a known city with a blank scrape field.
            if payload["location"]:
                existing.location = payload["location"]
            elif not existing.location:
                search_loc = (payload["raw_payload"] or {}).get("search_location")
                if search_loc:
                    existing.location = str(search_loc).strip().title()[:255]
            if payload["description"]:
                existing.description = payload["description"]
            existing.employment_type = payload["employment_type"]
            existing.salary_min = payload["salary_min"]
            existing.salary_max = payload["salary_max"]
            existing.currency = payload["currency"]
            if payload["posted_at"] is not None:
                existing.posted_at = payload["posted_at"]
            merged_raw = dict(existing.raw_payload or {})
            merged_raw.update(payload["raw_payload"] or {})
            existing.raw_payload = merged_raw
            if content_changed:
                clear_job_embedding(existing)
            updated += 1
            continue

        stmt = insert(Job).values(**payload)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_jobs_source_external_id",
            set_={
                "url_fingerprint": stmt.excluded.url_fingerprint,
                "title": stmt.excluded.title,
                "company": stmt.excluded.company,
                "url": stmt.excluded.url,
                "location": stmt.excluded.location,
                "description": stmt.excluded.description,
                "employment_type": stmt.excluded.employment_type,
                "salary_min": stmt.excluded.salary_min,
                "salary_max": stmt.excluded.salary_max,
                "currency": stmt.excluded.currency,
                "posted_at": stmt.excluded.posted_at,
                "raw_payload": stmt.excluded.raw_payload,
            },
        )
        db.execute(stmt)
        inserted += 1

    db.commit()
    logger.info(
        "job_ingest inserted=%s updated=%s skipped=%s batch_dupes=%s",
        inserted,
        updated,
        skipped,
        duplicates_in_batch,
    )
    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "batch_duplicates_dropped": duplicates_in_batch,
    }
