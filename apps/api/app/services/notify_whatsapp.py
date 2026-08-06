"""WhatsApp delivery via Twilio API."""

from __future__ import annotations

import logging
import re

import requests

from app.config import settings

logger = logging.getLogger(__name__)


def normalize_whatsapp_number(phone: str | None) -> str | None:
    """Normalize to E.164, then Twilio whatsapp: prefix."""
    if not phone:
        return None
    raw = phone.strip()
    digits = re.sub(r"[^\d+]", "", raw)
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if digits.startswith("+"):
        e164 = "+" + re.sub(r"\D", "", digits)
    else:
        only = re.sub(r"\D", "", digits)
        # Default India if 10-digit local number
        if len(only) == 10:
            e164 = f"+91{only}"
        elif len(only) >= 11:
            e164 = f"+{only}"
        else:
            return None
    if len(re.sub(r"\D", "", e164)) < 10:
        return None
    return f"whatsapp:{e164}"


def send_whatsapp(*, to_phone: str, body: str) -> str:
    """
    Send a WhatsApp message via Twilio.

    Returns status: sent | skipped | failed:...
    """
    if not settings.whatsapp_configured():
        logger.info("whatsapp_skipped reason=not_configured to=%s", to_phone)
        return "skipped"

    to_addr = normalize_whatsapp_number(to_phone)
    if not to_addr:
        logger.info("whatsapp_skipped reason=invalid_phone to=%s", to_phone)
        return "skipped:invalid_phone"

    url = (
        f"https://api.twilio.com/2010-04-01/Accounts/"
        f"{settings.twilio_account_sid}/Messages.json"
    )
    data = {
        "From": settings.twilio_whatsapp_from,
        "To": to_addr,
        "Body": body[:1500],
    }
    try:
        resp = requests.post(
            url,
            data=data,
            auth=(settings.twilio_account_sid or "", settings.twilio_auth_token or ""),
            timeout=30,
        )
        if resp.status_code >= 400:
            logger.warning(
                "whatsapp_failed status=%s body=%s",
                resp.status_code,
                resp.text[:300],
            )
            return f"failed:http_{resp.status_code}"
        logger.info("whatsapp_sent to=%s sid=%s", to_addr, resp.json().get("sid"))
        return "sent"
    except Exception as exc:  # noqa: BLE001
        logger.exception("whatsapp_failed to=%s", to_phone)
        msg = str(exc)
        if "101" in msg or "unreachable" in msg.lower():
            return "failed:network"
        if "timed out" in msg.lower():
            return "failed:timeout"
        return f"failed:{type(exc).__name__}"[:120]
