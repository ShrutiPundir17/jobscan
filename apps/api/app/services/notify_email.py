"""SMTP email delivery for match notifications."""

from __future__ import annotations

import logging
import smtplib
import socket
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger(__name__)


def _status_failed(exc: BaseException) -> str:
    """Short, DB-safe failure code (columns are VARCHAR(128))."""
    msg = str(exc)
    if "101" in msg or "unreachable" in msg.lower():
        return "failed:network"
    if "timed out" in msg.lower() or "timeout" in msg.lower():
        return "failed:timeout"
    if "authentication" in msg.lower() or "535" in msg:
        return "failed:auth"
    return f"failed:{type(exc).__name__}"[:120]


class _SMTP4(smtplib.SMTP):
    """SMTP that only dials IPv4 (avoids Railway Errno 101 on IPv6 AAAA)."""

    def _get_socket(self, host: str, port: int, timeout: float):  # noqa: ANN202
        last_exc: OSError | None = None
        for family, socktype, proto, _canon, sockaddr in socket.getaddrinfo(
            host, port, socket.AF_INET, socket.SOCK_STREAM
        ):
            sock = socket.socket(family, socktype, proto)
            if timeout is not None and timeout >= 0:
                sock.settimeout(timeout)
            try:
                sock.connect(sockaddr)
                return sock
            except OSError as exc:
                last_exc = exc
                try:
                    sock.close()
                except OSError:
                    pass
        if last_exc:
            raise last_exc
        raise OSError(f"No IPv4 address for SMTP host {host}")


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

    host = settings.smtp_host or ""
    port = int(settings.smtp_port or 587)
    try:
        with _SMTP4(host, port, timeout=30) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_username and settings.smtp_password:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(msg)
        logger.info("email_sent to=%s subject=%s", to_email, subject)
        return "sent"
    except Exception as exc:  # noqa: BLE001
        logger.exception("email_failed to=%s", to_email)
        return _status_failed(exc)
