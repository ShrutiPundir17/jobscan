"""Fill missing job.location from scrape metadata / URL when possible."""

from __future__ import annotations

import logging
import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Job

logger = logging.getLogger(__name__)

_IN_CITY = re.compile(r"-in-([a-z0-9-]+)(?:/|$)", re.I)


def _infer_location(job: Job) -> str | None:
    raw = job.raw_payload if isinstance(job.raw_payload, dict) else {}
    search_loc = raw.get("search_location")
    if isinstance(search_loc, str) and search_loc.strip():
        return search_loc.strip().title()

    url = job.url or ""
    match = _IN_CITY.search(url)
    if match:
        city = match.group(1).replace("-", " ").strip()
        if city and city.lower() not in {"india", "jobs", "internship", "internships"}:
            return city.title()
    return None


def backfill_missing_job_locations(db: Session, *, limit: int = 500) -> int:
    """Stamp location on jobs that are blank. Returns number updated."""
    rows = list(
        db.scalars(
            select(Job)
            .where(or_(Job.location.is_(None), Job.location == ""))
            .limit(limit)
        ).all()
    )
    updated = 0
    for job in rows:
        inferred = _infer_location(job)
        if not inferred:
            continue
        job.location = inferred[:255]
        updated += 1
    if updated:
        db.commit()
    logger.info("location_backfill updated=%s scanned=%s", updated, len(rows))
    return updated
