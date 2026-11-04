from __future__ import annotations

import logging
from typing import Any

from app.celery_app import celery

logger = logging.getLogger(__name__)


# No automatic retries: a retried submit could send the same application twice.
@celery.task(name="app.tasks.auto_apply.prepare_auto_apply")
def prepare_auto_apply(application_id: str, attempt: str) -> dict[str, Any]:
    """Open the ATS form (dry run), fill it, and save a plan for the user to approve."""
    from app.services.auto_apply import run_prepare

    logger.info("prepare_auto_apply app=%s attempt=%s", application_id, attempt)
    return run_prepare(application_id, attempt)


@celery.task(name="app.tasks.auto_apply.submit_auto_apply")
def submit_auto_apply(application_id: str, attempt: str) -> dict[str, Any]:
    """Submit an approved plan and confirm the ATS accepted it."""
    from app.services.auto_apply import run_submit

    logger.info("submit_auto_apply app=%s attempt=%s", application_id, attempt)
    return run_submit(application_id, attempt)
