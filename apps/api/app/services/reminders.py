"""
Follow-up reminders: turn due `applications.follow_up_at` into notifications.

Called from several places so reminders fire whatever the hosting setup:
Celery beat (worker mode), the GitHub Actions scan (inline mode), and lazily
when a user's notifications are fetched. Rows are claimed with
FOR UPDATE SKIP LOCKED and stamped `follow_up_notified_at` in the same
transaction, so concurrent callers can never send a reminder twice.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Application, ApplicationStatus, Notification

logger = logging.getLogger(__name__)

FOLLOW_UP_DUE = "follow_up_due"
# A reminder about an application that's closed or dismissed is just noise.
_SILENT_STATUSES = (ApplicationStatus.withdrawn, ApplicationStatus.rejected)


def fire_due_reminders(
    db: Session,
    *,
    user_id: UUID | None = None,
    now: datetime | None = None,
    deliver: Callable[[UUID], object] | None = None,
    limit: int = 200,
) -> int:
    """Create a notification per due reminder; `deliver(notification_id)` sends email/WhatsApp."""
    now = now or datetime.now(UTC)
    query = (
        select(Application)
        .options(joinedload(Application.job))
        .where(
            Application.follow_up_at.is_not(None),
            Application.follow_up_at <= now,
            Application.follow_up_notified_at.is_(None),
            Application.dismissed_at.is_(None),
            Application.status.not_in(_SILENT_STATUSES),
        )
        .order_by(Application.follow_up_at)
        .limit(limit)
        .with_for_update(of=Application, skip_locked=True)
    )
    if user_id is not None:
        query = query.where(Application.user_id == user_id)

    due = list(db.scalars(query).unique().all())
    created: list[UUID] = []
    for app in due:
        job = app.job
        where = f"{job.title} @ {job.company}" if job else "an application"
        lines = [f"You asked to be reminded to follow up on {where} (status: {app.status.value.replace('_', ' ')})."]
        if app.notes:
            lines.append(f"Your notes: {app.notes[:300]}")
        if job and job.url:
            lines.append(f"Job posting: {job.url}")
        note = Notification(
            user_id=app.user_id,
            type=FOLLOW_UP_DUE,
            title=f"Follow up: {where}"[:255],
            body="\n".join(lines),
            payload={"application_id": str(app.id), "job_id": str(app.job_id)},
            email_status="pending" if deliver else None,
            whatsapp_status="pending" if deliver else None,
        )
        db.add(note)
        app.follow_up_notified_at = now
        db.flush()
        created.append(note.id)
    db.commit()

    if deliver:
        for note_id in created:
            try:
                deliver(note_id)
            except Exception:  # noqa: BLE001 — the in-app notification already exists
                logger.exception("follow_up_delivery_failed notification=%s", note_id)
    if created:
        logger.info("follow_up_reminders_fired count=%s user=%s", len(created), user_id or "*")
    return len(created)
