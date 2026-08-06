"""Create in-app + email + WhatsApp notifications for new matches."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.db import SessionLocal
from app.models import Application, Notification, User
from app.services.notify_email import send_email
from app.services.notify_whatsapp import send_whatsapp

logger = logging.getLogger(__name__)

MATCHES_FOUND = "matches_found"


def _format_match_lines(apps: list[Application], *, limit: int = 5) -> list[str]:
    lines: list[str] = []
    for app in apps[:limit]:
        job = app.job
        score = app.match_score if app.match_score is not None else "?"
        verdict = app.match_verdict or "n/a"
        company = job.company if job else "?"
        title = job.title if job else "?"
        lines.append(f"- {title} @ {company} — {score}/100 ({verdict})")
    return lines


def create_match_notification(
    db: Session,
    user: User,
    applications: list[Application],
) -> Notification | None:
    """Persist an in-app notification summarizing newly saved matches."""
    apps = [a for a in applications if a.match_score is not None]
    if not apps:
        return None

    count = len(apps)
    top = max(a.match_score or 0 for a in apps)
    title = f"{count} new job match{'es' if count != 1 else ''} found"
    lines = _format_match_lines(apps)
    body = (
        f"JobAgent found {count} strong match{'es' if count != 1 else ''} "
        f"(top score {top}/100).\n\n"
        + "\n".join(lines)
        + f"\n\nReview them: {settings.app_public_url}"
    )
    payload: dict[str, Any] = {
        "application_ids": [str(a.id) for a in apps],
        "count": count,
        "top_score": top,
        "matches": [
            {
                "application_id": str(a.id),
                "job_id": str(a.job_id),
                "title": a.job.title if a.job else None,
                "company": a.job.company if a.job else None,
                "score": a.match_score,
                "verdict": a.match_verdict,
            }
            for a in apps
        ],
    }
    note = Notification(
        user_id=user.id,
        type=MATCHES_FOUND,
        title=title,
        body=body,
        payload=payload,
        email_status="pending",
        whatsapp_status="pending",
    )
    db.add(note)
    db.flush()
    return note


def deliver_notification(db: Session, notification_id: UUID) -> dict[str, str]:
    """Send email + WhatsApp for an existing notification row."""
    note = db.scalar(
        select(Notification)
        .options(joinedload(Notification.user))
        .where(Notification.id == notification_id)
    )
    if note is None:
        return {"email": "failed:not_found", "whatsapp": "failed:not_found"}

    user = note.user
    email_status = "skipped"
    whatsapp_status = "skipped"

    if user.notify_email_enabled:
        email_status = send_email(
            to_email=user.email,
            subject=note.title,
            text_body=note.body,
            html_body="<pre style='font-family:sans-serif'>"
            + note.body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            + "</pre>",
        )
    else:
        email_status = "skipped:disabled"

    if user.notify_whatsapp_enabled:
        if user.phone:
            whatsapp_status = send_whatsapp(to_phone=user.phone, body=note.body)
        else:
            whatsapp_status = "skipped:no_phone"
    else:
        whatsapp_status = "skipped:disabled"

    note.email_status = (email_status or "")[:128]
    note.whatsapp_status = (whatsapp_status or "")[:128]
    db.add(note)
    db.commit()
    return {"email": email_status, "whatsapp": whatsapp_status}


def notify_user_of_matches(user_id: UUID, application_ids: list[str]) -> dict[str, Any]:
    """
    Create + deliver a matches_found notification.

    Intended to run in a Celery worker after Stage 2 persists applications.
    """
    if not application_ids:
        return {"status": "noop", "reason": "no_applications"}

    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user is None:
            return {"status": "failed", "reason": "user_not_found"}

        ids = [UUID(x) for x in application_ids]
        apps = list(
            db.scalars(
                select(Application)
                .options(joinedload(Application.job))
                .where(
                    Application.user_id == user_id,
                    Application.id.in_(ids),
                )
                .order_by(Application.match_score.desc())
            ).all()
        )
        note = create_match_notification(db, user, apps)
        if note is None:
            db.rollback()
            return {"status": "noop", "reason": "no_scored_apps"}
        db.commit()
        delivery = deliver_notification(db, note.id)
        return {
            "status": "ok",
            "notification_id": str(note.id),
            "email": delivery["email"],
            "whatsapp": delivery["whatsapp"],
            "count": len(apps),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("notify_user_of_matches failed user=%s", user_id)
        db.rollback()
        return {"status": "failed", "reason": str(exc)}
    finally:
        db.close()


def send_test_notification(user_id: UUID) -> dict[str, Any]:
    """Create + deliver a test notification so users can verify channels."""
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user is None:
            return {"status": "failed", "reason": "user_not_found"}

        body = (
            "This is a JobAgent test alert.\n\n"
            "If you received this, your notification channel is working.\n"
            f"Email pref: {'on' if user.notify_email_enabled else 'off'}\n"
            f"WhatsApp pref: {'on' if user.notify_whatsapp_enabled else 'off'}\n"
            f"Phone: {user.phone or '(not set)'}\n\n"
            f"Open dashboard: {settings.app_public_url}"
        )
        note = Notification(
            user_id=user.id,
            type="test",
            title="JobAgent test notification",
            body=body,
            payload={
                "kind": "test",
                "email_configured": settings.email_configured(),
                "whatsapp_configured": settings.whatsapp_configured(),
            },
            email_status="pending",
            whatsapp_status="pending",
        )
        db.add(note)
        db.commit()
        delivery = deliver_notification(db, note.id)
        return {
            "status": "ok",
            "notification_id": str(note.id),
            "email": delivery["email"],
            "whatsapp": delivery["whatsapp"],
            "platform": {
                "email_configured": settings.email_configured(),
                "whatsapp_configured": settings.whatsapp_configured(),
            },
            "hint": _delivery_hint(delivery["email"], delivery["whatsapp"]),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("send_test_notification failed user=%s", user_id)
        db.rollback()
        return {"status": "failed", "reason": str(exc)}
    finally:
        db.close()


def _delivery_hint(email_status: str, whatsapp_status: str) -> str:
    parts: list[str] = []
    if email_status == "skipped":
        parts.append("Email skipped: platform SMTP is not configured in server .env.")
    elif email_status.startswith("skipped:"):
        parts.append(f"Email {email_status}.")
    elif email_status == "sent":
        parts.append("Email sent.")
    elif email_status in {"failed:timeout", "failed:network"}:
        parts.append(
            "Email failed: Gmail SMTP is blocked from this cloud host. "
            "Add RESEND_API_KEY (https://resend.com) on the API service, set "
            "SMTP_FROM_EMAIL=onboarding@resend.dev, and redeploy."
        )
    elif email_status.startswith("failed"):
        parts.append(f"Email failed ({email_status}).")

    if whatsapp_status == "skipped":
        parts.append("WhatsApp skipped: platform Twilio is not configured in server .env.")
    elif whatsapp_status == "skipped:disabled":
        parts.append("WhatsApp skipped: turn on the WhatsApp toggle and save.")
    elif whatsapp_status == "skipped:no_phone":
        parts.append("WhatsApp skipped: add a phone number and save.")
    elif whatsapp_status == "skipped:invalid_phone":
        parts.append("WhatsApp skipped: use international format like +91XXXXXXXXXX.")
    elif whatsapp_status == "sent":
        parts.append("WhatsApp sent.")
    elif whatsapp_status.startswith("failed"):
        parts.append(f"WhatsApp failed ({whatsapp_status}).")

    return " ".join(parts) if parts else "Done."
