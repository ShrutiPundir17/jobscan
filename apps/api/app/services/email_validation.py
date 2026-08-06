from __future__ import annotations

from email_validator import EmailNotValidError, validate_email

# Domains reserved for docs/tests — never allow signup even if DNS changes.
BLOCKED_EMAIL_DOMAINS = frozenset(
    {
        "example.com",
        "example.org",
        "example.net",
        "example.edu",
        "test.com",
        "test.org",
        "localhost",
        "invalid",
        "local",
    }
)


class InvalidEmailError(ValueError):
    """Raised when an email fails format or deliverability checks."""


def normalize_and_validate_email(value: str, *, check_deliverability: bool = True) -> str:
    """
    Validate an email for signup.

    - Normalizes casing/unicode
    - Rejects reserved/documentation domains
    - When check_deliverability=True, verifies the domain can accept mail (DNS/MX)
    """
    raw = (value or "").strip()
    if not raw:
        raise InvalidEmailError("Email is required")

    try:
        info = validate_email(raw, check_deliverability=check_deliverability)
    except EmailNotValidError as exc:
        raise InvalidEmailError(_friendly_message(str(exc))) from exc

    normalized = info.normalized
    domain = normalized.rsplit("@", 1)[-1].lower()

    if domain in BLOCKED_EMAIL_DOMAINS or domain.endswith(".local") or domain.endswith(".test"):
        raise InvalidEmailError(
            "Please use a real email address from a mailbox you can access."
        )

    local = normalized.split("@", 1)[0]
    if len(local) < 2:
        raise InvalidEmailError("Please enter a valid email address.")

    return normalized


def _friendly_message(raw: str) -> str:
    text = raw.strip()
    lowered = text.lower()
    if "does not accept email" in lowered or "does not exist" in lowered:
        return "Please use a real email address. This domain cannot receive mail."
    if "must have an @-sign" in lowered or "not a valid email" in lowered:
        return "Please enter a valid email address (e.g. name@gmail.com)."
    return text or "Please enter a valid email address."
