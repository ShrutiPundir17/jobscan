from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select

from app.celery_app import celery
from app.config import settings
from app.db import SessionLocal
from app.models import Job
from app.services.embeddings import embed_job

logger = logging.getLogger(__name__)


@celery.task(
    name="app.tasks.job_embeddings.embed_pending_jobs",
    soft_time_limit=10 * 60,
    time_limit=12 * 60,
)
def embed_pending_jobs(batch_size: int | None = None) -> dict[str, Any]:
    """
    Embed jobs that are missing vectors (Stage 1 prerequisite).

    Runs after scans and on a light schedule so similarity search has coverage.
    """
    if not settings.google_api_key:
        logger.warning("embed_pending_jobs skipped: GOOGLE_API_KEY not set")
        return {"status": "skipped", "reason": "no_google_api_key", "embedded": 0}

    limit = max(1, min(batch_size or settings.embed_jobs_batch_size, 200))
    db = SessionLocal()
    embedded = 0
    failed = 0
    errors: list[str] = []

    try:
        pending = db.scalars(
            select(Job)
            .where(Job.embedding.is_(None))
            .order_by(Job.created_at.desc())
            .limit(limit)
        ).all()

        remaining = db.scalar(
            select(func.count()).select_from(Job).where(Job.embedding.is_(None))
        ) or 0

        for job in pending:
            try:
                embed_job(db, job, commit=True)
                embedded += 1
            except HTTPException as exc:
                failed += 1
                msg = f"{job.id}: {exc.detail}"
                errors.append(msg)
                logger.warning("embed_job_failed %s", msg)
                db.rollback()
            except Exception as exc:  # noqa: BLE001
                failed += 1
                msg = f"{job.id}: {exc}"
                errors.append(msg)
                logger.exception("embed_job_failed %s", msg)
                db.rollback()

        summary = {
            "status": "ok" if failed == 0 else "partial",
            "embedded": embedded,
            "failed": failed,
            "pending_remaining": max(0, remaining - embedded),
            "batch_size": limit,
            "errors": errors[:10],
        }
        logger.info(
            "embed_pending_jobs embedded=%s failed=%s remaining≈%s",
            embedded,
            failed,
            summary["pending_remaining"],
        )
        return summary
    finally:
        db.close()
