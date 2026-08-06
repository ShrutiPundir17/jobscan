from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.celery_app import celery

logger = logging.getLogger(__name__)


@celery.task(name="app.tasks.notify_matches.notify_matches_found", bind=True, max_retries=2)
def notify_matches_found(self, user_id: str, application_ids: list[str]) -> dict[str, Any]:
    """Send in-app + email + WhatsApp when Stage 2 persists new matches."""
    from app.services.notify_matches import notify_user_of_matches

    logger.info(
        "notify_matches_found user=%s apps=%s",
        user_id,
        len(application_ids or []),
    )
    try:
        return notify_user_of_matches(UUID(user_id), list(application_ids or []))
    except Exception as exc:  # noqa: BLE001
        logger.exception("notify_matches_found failed")
        raise self.retry(exc=exc, countdown=30) from exc
