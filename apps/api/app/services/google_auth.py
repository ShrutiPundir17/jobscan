"""Google Sign-In helpers — verify GIS ID tokens or OAuth access tokens."""

from __future__ import annotations

import logging
from typing import Any

import requests

from app.config import settings

logger = logging.getLogger(__name__)


class GoogleAuthError(Exception):
    """Invalid or unverifiable Google credential."""


def google_oauth_configured() -> bool:
    return bool((settings.google_oauth_client_id or "").strip())


def verify_google_id_token(token: str) -> dict[str, Any]:
    """Verify a Google Identity Services ID token and return claims."""
    client_id = (settings.google_oauth_client_id or "").strip()
    if not client_id:
        raise GoogleAuthError("Google Sign-In is not configured on the server.")

    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token
    except ImportError as exc:  # pragma: no cover
        raise GoogleAuthError("google-auth package is not installed.") from exc

    try:
        claims = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            audience=client_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("google_id_token_invalid err=%s", exc)
        raise GoogleAuthError("Invalid Google sign-in token.") from exc

    if claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
        raise GoogleAuthError("Invalid Google token issuer.")
    if not claims.get("email"):
        raise GoogleAuthError("Google account did not return an email.")
    if claims.get("email_verified") is False:
        raise GoogleAuthError("Google email is not verified.")
    return claims


def fetch_google_userinfo(access_token: str) -> dict[str, Any]:
    """Resolve profile from a Google OAuth access token."""
    try:
        resp = requests.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
    except requests.RequestException as exc:
        logger.warning("google_userinfo_failed err=%s", exc)
        raise GoogleAuthError("Could not reach Google to verify sign-in.") from exc

    if resp.status_code >= 400:
        logger.warning("google_userinfo_http status=%s body=%s", resp.status_code, resp.text[:200])
        raise GoogleAuthError("Invalid Google access token.")

    data = resp.json()
    if not data.get("email"):
        raise GoogleAuthError("Google account did not return an email.")
    if data.get("email_verified") is False or data.get("verified_email") is False:
        raise GoogleAuthError("Google email is not verified.")
    return data


def resolve_google_profile(
    *,
    id_token_value: str | None,
    access_token: str | None,
) -> dict[str, str]:
    """
    Return normalized profile: email, full_name, google_sub.
    Accepts GIS ID token and/or OAuth access token.
    """
    if not google_oauth_configured():
        raise GoogleAuthError("Google Sign-In is not configured on the server.")

    claims: dict[str, Any] | None = None
    if id_token_value and id_token_value.strip():
        claims = verify_google_id_token(id_token_value.strip())
    elif access_token and access_token.strip():
        claims = fetch_google_userinfo(access_token.strip())
    else:
        raise GoogleAuthError("Missing Google credential.")

    email = str(claims.get("email") or "").lower().strip()
    if not email:
        raise GoogleAuthError("Google account did not return an email.")

    name = (
        str(claims.get("name") or "").strip()
        or str(claims.get("given_name") or "").strip()
        or None
    )
    sub = str(claims.get("sub") or "").strip() or None
    return {
        "email": email,
        "full_name": name or "",
        "google_sub": sub or "",
    }
