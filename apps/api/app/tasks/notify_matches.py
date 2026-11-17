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


@celery.task(name="app.tasks.notify_matches.deliver_notification_task")
def deliver_notification_task(notification_id: str) -> dict[str, Any]:
    """Send email/WhatsApp for an existing in-app notification (best effort, no retry)."""
    from app.db import SessionLocal
    from app.services.notify_matches import deliver_notification

    db = SessionLocal()
    try:
        return deliver_notification(db, UUID(notification_id))
    finally:
        db.close()


def queue_delivery(notification_id: UUID) -> None:
    from app.tasks.dispatch import dispatch

    dispatch(deliver_notification_task, str(notification_id))


@celery.task(name="app.tasks.notify_matches.fire_due_reminders")
def fire_due_reminders_task() -> dict[str, Any]:
    """Beat: turn due follow-up reminders into notifications (worker mode)."""
    from app.db import SessionLocal
    from app.services.reminders import fire_due_reminders

    db = SessionLocal()
    try:
        return {"fired": fire_due_reminders(db, deliver=queue_delivery)}
    finally:
        db.close()
