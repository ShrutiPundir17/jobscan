"""Email delivery: Resend (HTTPS) preferred on Railway; SMTP fallback."""

from __future__ import annotations

import logging
import smtplib
import socket
import ssl
from email.message import EmailMessage

import requests

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


def _smtp_password() -> str:
    # Gmail app passwords are often stored with spaces; SMTP expects 16 chars.
    return (settings.smtp_password or "").replace(" ", "")


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


class _SMTP4_SSL(smtplib.SMTP_SSL):
    """SMTP_SSL over IPv4 only."""

    def _get_socket(self, host: str, port: int, timeout: float):  # noqa: ANN202
        last_exc: OSError | None = None
        context = self.context if self.context else ssl.create_default_context()
        for family, socktype, proto, _canon, sockaddr in socket.getaddrinfo(
            host, port, socket.AF_INET, socket.SOCK_STREAM
        ):
            sock = socket.socket(family, socktype, proto)
            if timeout is not None and timeout >= 0:
                sock.settimeout(timeout)
            try:
                sock.connect(sockaddr)
                return context.wrap_socket(sock, server_hostname=host)
            except OSError as exc:
                last_exc = exc
                try:
                    sock.close()
                except OSError:
                    pass
        if last_exc:
            raise last_exc
        raise OSError(f"No IPv4 address for SMTP host {host}")


def _send_via_resend(*, to_email: str, subject: str, text_body: str, html_body: str | None) -> str:
    from_name = settings.smtp_from_name or "JobAgent"
    from_addr = settings.smtp_from_email or "onboarding@resend.dev"
    payload = {
        "from": f"{from_name} <{from_addr}>",
        "to": [to_email],
        "subject": subject,
        "text": text_body,
    }
    if html_body:
        payload["html"] = html_body
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {settings.resend_api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    if resp.status_code >= 400:
        logger.warning("resend_failed status=%s body=%s", resp.status_code, resp.text[:300])
        return f"failed:http_{resp.status_code}"
    logger.info("email_sent provider=resend to=%s subject=%s", to_email, subject)
    return "sent"


def _send_via_smtp(*, to_email: str, subject: str, text_body: str, html_body: str | None) -> str:
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
    password = _smtp_password()
    username = settings.smtp_username

    def _login_and_send(smtp: smtplib.SMTP) -> None:
        if username and password:
            smtp.login(username, password)
        smtp.send_message(msg)

    # Prefer explicit SSL on 465 when configured; otherwise STARTTLS on 587,
    # then fall back to 465 if STARTTLS path fails (common on cloud hosts).
    try:
        if port == 465:
            with _SMTP4_SSL(host, 465, timeout=30) as smtp:
                _login_and_send(smtp)
        else:
            with _SMTP4(host, port, timeout=30) as smtp:
                if settings.smtp_use_tls:
                    smtp.starttls()
                _login_and_send(smtp)
        logger.info("email_sent provider=smtp to=%s subject=%s", to_email, subject)
        return "sent"
    except Exception as first:  # noqa: BLE001
        if port == 465:
            raise first
        logger.warning("smtp_%s_failed trying_465 err=%s", port, first)
        with _SMTP4_SSL(host, 465, timeout=30) as smtp:
            _login_and_send(smtp)
        logger.info("email_sent provider=smtp465 to=%s subject=%s", to_email, subject)
        return "sent"


def send_email(*, to_email: str, subject: str, text_body: str, html_body: str | None = None) -> str:
    """
    Send an email via Resend (preferred) or SMTP.

    Returns status: sent | skipped | failed:...
    """
    if not settings.email_configured():
        logger.info("email_skipped reason=not_configured to=%s subject=%s", to_email, subject)
        return "skipped"

    try:
        if settings.resend_api_key:
            return _send_via_resend(
                to_email=to_email,
                subject=subject,
                text_body=text_body,
                html_body=html_body,
            )
        return _send_via_smtp(
            to_email=to_email,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("email_failed to=%s", to_email)
        return _status_failed(exc)
