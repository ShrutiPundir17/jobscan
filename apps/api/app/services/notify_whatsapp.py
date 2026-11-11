"""WhatsApp delivery via Twilio API.

Production WhatsApp only allows free-form text inside the 24-hour window after
the user last messaged you; anything else must be a pre-approved template
(otherwise Twilio accepts the message and WhatsApp drops it with error 63016).
So each message kind can be bound to a Twilio Content Template SID
(TWILIO_TEMPLATE_*); without one we fall back to free-form text, which is what
the Twilio sandbox uses. Template texts to submit: docs/phase-5.md.
"""

from __future__ import annotations

import json
import logging
import re

import requests

from app.config import settings

logger = logging.getLogger(__name__)

# Message kind -> settings attribute holding its Content Template SID (HX...).
TEMPLATE_SETTINGS = {
    "matches_found": "twilio_template_matches_sid",
    "password_reset": "twilio_template_password_reset_sid",
    "login_email": "twilio_template_login_email_sid",
}

# Twilio error codes worth a readable status (stored on the notification row).
_TWILIO_ERRORS = {
    63016: "needs_template",  # outside the 24h window and no template used
    21211: "invalid_number",
    21614: "invalid_number",
    63003: "invalid_number",
    63007: "sender_not_whatsapp",
    21608: "sandbox_not_joined",
    63015: "sandbox_not_joined",
    63018: "rate_limited",
    20003: "bad_credentials",
    21656: "bad_template_variables",
    63027: "template_not_found",
}

_VARIABLE_MAX = 200


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


def mask_phone(phone: str | None) -> str:
    """Keep only the last 4 digits in logs — phone numbers are personal data."""
    digits = re.sub(r"\D", "", phone or "")
    return f"***{digits[-4:]}" if digits else "-"


def template_variable(value: object) -> str:
    """
    WhatsApp rejects template variables that are empty or contain newlines,
    tabs or runs of 4+ spaces; keep them short and single-line.
    """
    text = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    if len(text) > _VARIABLE_MAX:
        text = text[: _VARIABLE_MAX - 1].rstrip() + "…"
    return text or "-"


def template_sid(kind: str | None) -> str | None:
    attr = TEMPLATE_SETTINGS.get(kind or "")
    sid = (getattr(settings, attr, "") or "").strip() if attr else ""
    return sid or None


def _twilio_error_status(resp: requests.Response) -> str:
    try:
        code = int(resp.json().get("code") or 0)
    except (ValueError, AttributeError):
        code = 0
    if code in _TWILIO_ERRORS:
        return f"failed:{_TWILIO_ERRORS[code]}"
    return f"failed:twilio_{code}" if code else f"failed:http_{resp.status_code}"


def send_whatsapp(
    *,
    to_phone: str,
    body: str,
    kind: str | None = None,
    variables: list[object] | None = None,
) -> str:
    """
    Send a WhatsApp message via Twilio.

    If `kind` has a Content Template configured, sends the template with
    `variables` ({{1}}, {{2}}, …); otherwise sends `body` as free-form text.
    Returns status: sent | skipped | skipped:invalid_phone | failed:<reason>.
    "sent" means Twilio accepted it; WhatsApp delivery is asynchronous.
    """
    if not settings.whatsapp_configured():
        logger.info("whatsapp_skipped reason=not_configured to=%s", mask_phone(to_phone))
        return "skipped"

    to_addr = normalize_whatsapp_number(to_phone)
    if not to_addr:
        logger.info("whatsapp_skipped reason=invalid_phone to=%s", mask_phone(to_phone))
        return "skipped:invalid_phone"

    url = (
        f"https://api.twilio.com/2010-04-01/Accounts/"
        f"{settings.twilio_account_sid}/Messages.json"
    )
    data: dict[str, str] = {"From": settings.twilio_whatsapp_from or "", "To": to_addr}
    sid = template_sid(kind)
    if sid:
        data["ContentSid"] = sid
        data["ContentVariables"] = json.dumps(
            {str(i): template_variable(v) for i, v in enumerate(variables or [], start=1)}
        )
    else:
        data["Body"] = body[:1500]

    try:
        resp = requests.post(
            url,
            data=data,
            auth=(settings.twilio_account_sid or "", settings.twilio_auth_token or ""),
            timeout=30,
        )
        if resp.status_code >= 400:
            status = _twilio_error_status(resp)
            logger.warning(
                "whatsapp_failed to=%s kind=%s template=%s status=%s",
                mask_phone(to_phone), kind, bool(sid), status,
            )
            return status
        logger.info(
            "whatsapp_sent to=%s kind=%s template=%s sid=%s",
            mask_phone(to_phone), kind, bool(sid), resp.json().get("sid"),
        )
        return "sent"
    except Exception as exc:  # noqa: BLE001
        logger.warning("whatsapp_failed to=%s err=%s", mask_phone(to_phone), type(exc).__name__)
        msg = str(exc)
        if "101" in msg or "unreachable" in msg.lower():
            return "failed:network"
        if "timed out" in msg.lower():
            return "failed:timeout"
        return f"failed:{type(exc).__name__}"[:120]
