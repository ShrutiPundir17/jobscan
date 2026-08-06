from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db import get_db
from app.models import Notification, User
from app.services.notify_matches import send_test_notification

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    title: str
    body: str
    payload: dict | None = None
    read_at: datetime | None = None
    email_status: str | None = None
    whatsapp_status: str | None = None
    created_at: datetime


class NotificationListResponse(BaseModel):
    items: list[NotificationResponse]
    total: int
    unread_count: int
    limit: int
    offset: int


class TestNotificationResponse(BaseModel):
    status: str
    notification_id: str | None = None
    email: str | None = None
    whatsapp: str | None = None
    platform: dict | None = None
    hint: str | None = None
    reason: str | None = None


@router.get("", response_model=NotificationListResponse)
def list_notifications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    unread_only: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> NotificationListResponse:
    """List notifications (new matches, status updates, reminders)."""
    filters = [Notification.user_id == current_user.id]
    if unread_only:
        filters.append(Notification.read_at.is_(None))

    total = int(
        db.scalar(select(func.count()).select_from(Notification).where(*filters)) or 0
    )
    unread_count = int(
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.user_id == current_user.id,
                Notification.read_at.is_(None),
            )
        )
        or 0
    )
    rows = list(
        db.scalars(
            select(Notification)
            .where(*filters)
            .order_by(Notification.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).all()
    )
    return NotificationListResponse(
        items=[NotificationResponse.model_validate(n) for n in rows],
        total=total,
        unread_count=unread_count,
        limit=limit,
        offset=offset,
    )


@router.post("/test", response_model=TestNotificationResponse)
def test_notification(
    current_user: User = Depends(get_current_user),
) -> TestNotificationResponse:
    """
    Send a test email/WhatsApp using the current user's prefs.

    Useful to verify channel setup. Does not require new job matches.
    """
    result = send_test_notification(current_user.id)
    return TestNotificationResponse(**result)


@router.patch("/{notification_id}/read", response_model=NotificationResponse)
def mark_notification_read(
    notification_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Notification:
    """Mark a notification as read."""
    note = db.scalar(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == current_user.id,
        )
    )
    if note is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    if note.read_at is None:
        from datetime import UTC, datetime as dt

        note.read_at = dt.now(UTC)
        db.add(note)
        db.commit()
        db.refresh(note)
    return note
