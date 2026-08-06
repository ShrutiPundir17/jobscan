"""SMTP email delivery for match notifications."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger(__name__)


def send_email(*, to_email: str, subject: str, text_body: str, html_body: str | None = None) -> str:
    """
    Send an email via SMTP.

    Returns status: sent | skipped | failed:...
    """
    if not settings.email_configured():
        logger.info("email_skipped reason=not_configured to=%s subject=%s", to_email, subject)
        return "skipped"

    msg = EmailMessage()
    from_name = settings.smtp_from_name or "JobAgent"
    from_addr = settings.smtp_from_email or ""
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_addr}>"
    msg["To"] = to_email
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP(settings.smtp_host or "", settings.smtp_port, timeout=30) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_username and settings.smtp_password:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(msg)
        logger.info("email_sent to=%s subject=%s", to_email, subject)
        return "sent"
    except Exception as exc:  # noqa: BLE001
        logger.exception("email_failed to=%s", to_email)
        return f"failed:{exc}"
