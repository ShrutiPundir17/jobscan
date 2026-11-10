"""
Run one job scan to completion, then embed the new jobs — no Celery worker.

Used by the scheduled GitHub Actions workflow (.github/workflows/scan.yml) when
the API runs on a host without a background worker (TASK_MODE=inline).

    python -m app.scan_once                       # roles/cities from users' preferences
    python -m app.scan_once --keywords "python developer" --locations "pune,delhi"

Exit code 1 only when the scan itself failed; "blocked"/"partial"/"skipped"
are normal outcomes (the portal cooldowns handle them).
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import User
from app.tasks.job_scanner import run_scan_job

logger = logging.getLogger("app.scan_once")

MAX_EMBED_BATCHES = 5


def _csv(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def preferred_search_terms() -> tuple[list[str] | None, list[str] | None]:
    """Most common target roles / preferred cities across users (None = scanner defaults)."""
    roles: Counter[str] = Counter()
    cities: Counter[str] = Counter()
    names: dict[str, str] = {}
    db = SessionLocal()
    try:
        for target_roles, preferred_locations in db.execute(select(User.target_roles, User.preferred_locations)):
            for bucket, values in ((roles, target_roles), (cities, preferred_locations)):
                for value in values or []:
                    text = " ".join(str(value).split())
                    if text:
                        bucket[text.lower()] += 1
                        names.setdefault(text.lower(), text)
    finally:
        db.close()
    top_roles = [names[k] for k, _ in roles.most_common(max(1, settings.scanner_max_keywords))]
    top_cities = [names[k] for k, _ in cities.most_common(max(1, settings.scanner_max_locations))]
    return (top_roles or None), (top_cities or None)


def embed_new_jobs() -> None:
    """Embed synchronously: a background thread would die with this process."""
    from app.tasks.job_embeddings import embed_pending_jobs

    for _ in range(MAX_EMBED_BATCHES):
        result = embed_pending_jobs()  # runs the task body in-process
        logger.info("embed_batch %s", result)
        if not result or result.get("embedded", 0) == 0 or result.get("pending_remaining", 0) == 0:
            break


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one JobAgent job scan.")
    parser.add_argument("--keywords", help="Comma-separated roles (default: from users' preferences)")
    parser.add_argument("--locations", help="Comma-separated cities (default: from users' preferences)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    keywords, locations = _csv(args.keywords) or None, _csv(args.locations) or None
    if keywords is None and locations is None:
        keywords, locations = preferred_search_terms()
    logger.info("scan_once keywords=%s locations=%s", keywords, locations)

    result = run_scan_job(keywords, locations, after_scan=embed_new_jobs)
    print(json.dumps(result, indent=2, default=str))
    return 1 if result.get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
