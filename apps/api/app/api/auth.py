import hashlib
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import settings
from app.db import get_db
from app.models import PasswordResetToken, User
from app.schemas.auth import (
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    ForgotUsernameRequest,
    ForgotUsernameResponse,
    GoogleAuthRequest,
    GoogleConfigResponse,
    ResetPasswordRequest,
    TokenResponse,
    UserLogin,
    UserRegister,
    UserResponse,
)
from app.security import create_access_token, hash_password, verify_password
from app.services.google_auth import GoogleAuthError, google_oauth_configured, resolve_google_profile
from app.services.notify_email import send_email

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

RESET_TOKEN_TTL = timedelta(hours=1)
GENERIC_FORGOT_PASSWORD_MSG = (
    "If an account exists for that email, we sent password reset instructions."
)
GENERIC_FORGOT_USERNAME_MSG = (
    "If an account matches that phone number, we sent your login email."
)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _phone_digits(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\D", "", value.strip())


def _phones_match(stored: str | None, submitted: str) -> bool:
    a = _phone_digits(stored)
    b = _phone_digits(submitted)
    if not a or not b:
        return False
    if a == b:
        return True
    # Compare last 10 digits (common for India local vs +91).
    return len(a) >= 10 and len(b) >= 10 and a[-10:] == b[-10:]


def _public_reset_url(token: str) -> str:
    base = (settings.app_public_url or "http://localhost:5173").rstrip("/")
    return f"{base}/?reset_token={token}"


def _try_send_email(*, to_email: str, subject: str, text_body: str, html_body: str) -> str:
    """
    Prefer Resend. In production, skip Gmail SMTP (usually blocked on Railway)
    so callers can fall back to WhatsApp quickly.
    """
    if settings.resend_api_key and settings.smtp_from_email:
        return send_email(
            to_email=to_email,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
        )
    if settings.app_env == "production":
        logger.info("email_skipped reason=no_resend_in_production to=%s", to_email)
        return "skipped:no_resend"
    return send_email(
        to_email=to_email,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
    )


def _try_send_whatsapp(*, phone: str | None, body: str) -> str:
    if not phone or not settings.whatsapp_configured():
        return "skipped"
    from app.services.notify_whatsapp import send_whatsapp

    return send_whatsapp(to_phone=phone, body=body)


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(payload: UserRegister, db: Session = Depends(get_db)) -> User:
    email = payload.email.lower().strip()
    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = User(
        email=email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name.strip() if payload.full_name else None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
def login(payload: UserLogin, db: Session = Depends(get_db)) -> TokenResponse:
    email = payload.email.lower().strip()
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    token = create_access_token(subject=user.id)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
def forgot_password(
    payload: ForgotPasswordRequest,
    db: Session = Depends(get_db),
) -> ForgotPasswordResponse:
    email = str(payload.email).lower().strip()
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        return ForgotPasswordResponse(message=GENERIC_FORGOT_PASSWORD_MSG)

    raw_token = secrets.token_urlsafe(32)
    row = PasswordResetToken(
        user_id=user.id,
        token_hash=_hash_token(raw_token),
        expires_at=datetime.now(timezone.utc) + RESET_TOKEN_TTL,
    )
    db.add(row)
    db.commit()

    reset_url = _public_reset_url(raw_token)
    text_body = (
        "Reset your JobAgent password using this link (expires in 1 hour):\n\n"
        f"{reset_url}\n\n"
        "If you did not request this, you can ignore this message."
    )
    html_body = (
        "<p>Reset your JobAgent password using this link "
        "(expires in 1 hour):</p>"
        f'<p><a href="{reset_url}">{reset_url}</a></p>'
        "<p>If you did not request this, you can ignore this message.</p>"
    )
    wa_body = (
        "JobAgent password reset (expires in 1 hour):\n"
        f"{reset_url}\n"
        "If you did not request this, ignore this message."
    )

    email_status = _try_send_email(
        to_email=user.email,
        subject="Reset your JobAgent password",
        text_body=text_body,
        html_body=html_body,
    )
    if email_status == "sent":
        return ForgotPasswordResponse(
            message="If an account exists for that email, we sent password reset instructions."
        )

    wa_status = _try_send_whatsapp(phone=user.phone, body=wa_body)
    if wa_status == "sent":
        return ForgotPasswordResponse(
            message=(
                "Password reset email isn't available on this server yet "
                "(cloud hosts block Gmail SMTP). We sent the reset link to your WhatsApp instead."
            )
        )

    # Local/dev when neither channel works — still let the owner continue.
    if settings.app_env != "production":
        logger.info(
            "forgot_password falling back to inline token email=%s email_status=%s wa_status=%s",
            email,
            email_status,
            wa_status,
        )
        return ForgotPasswordResponse(
            message=(
                "We couldn't send email or WhatsApp. Use the reset link below to continue "
                "(add RESEND_API_KEY or a phone number in Preferences for automatic delivery)."
            ),
            reset_token=raw_token,
            reset_url=reset_url,
        )

    logger.warning(
        "forgot_password delivery failed email=%s email_status=%s wa_status=%s",
        email,
        email_status,
        wa_status,
    )
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            "Could not send a reset link. Add a phone number in Preferences "
            "(WhatsApp) or configure Resend email, then try again."
        ),
    )


@router.post("/reset-password", response_model=dict)
def reset_password(
    payload: ResetPasswordRequest,
    db: Session = Depends(get_db),
) -> dict:
    token_hash = _hash_token(payload.token.strip())
    row = db.scalar(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    )
    now = datetime.now(timezone.utc)
    if row is None or row.used_at is not None or row.expires_at < now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset link. Request a new one.",
        )

    user = db.get(User, row.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset link. Request a new one.",
        )

    user.hashed_password = hash_password(payload.new_password)
    row.used_at = now
    # Invalidate other unused tokens for this user.
    others = db.scalars(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.id != row.id,
        )
    ).all()
    for other in others:
        other.used_at = now
    db.commit()
    return {"message": "Password updated. You can sign in with your new password."}


@router.post("/forgot-username", response_model=ForgotUsernameResponse)
def forgot_username(
    payload: ForgotUsernameRequest,
    db: Session = Depends(get_db),
) -> ForgotUsernameResponse:
    phone = payload.phone.strip()
    candidates = db.scalars(select(User).where(User.phone.is_not(None))).all()
    user = next((u for u in candidates if _phones_match(u.phone, phone)), None)
    if user is None:
        return ForgotUsernameResponse(message=GENERIC_FORGOT_USERNAME_MSG)

    text_body = (
        "You requested your JobAgent login email.\n\n"
        f"Your login email is: {user.email}\n\n"
        "Sign in at JobAgent with this email and your password.\n"
        "If you did not request this, you can ignore this message."
    )
    html_body = (
        "<p>You requested your JobAgent login email.</p>"
        f"<p>Your login email is: <strong>{user.email}</strong></p>"
        "<p>Sign in at JobAgent with this email and your password.</p>"
        "<p>If you did not request this, you can ignore this message.</p>"
    )
    wa_body = (
        f"Your JobAgent login email is: {user.email}\n"
        "Sign in with this email and your password."
    )

    # Prefer WhatsApp for username recovery (user already proved phone ownership).
    wa_status = _try_send_whatsapp(phone=user.phone, body=wa_body)
    if wa_status == "sent":
        return ForgotUsernameResponse(
            message="If an account matches that phone number, we sent your login email."
        )

    email_status = _try_send_email(
        to_email=user.email,
        subject="Your JobAgent login email",
        text_body=text_body,
        html_body=html_body,
    )
    if email_status == "sent":
        return ForgotUsernameResponse(
            message="If an account matches that phone number, we sent your login email."
        )

    # Matched phone but no outbound channel — show email so recovery still works.
    logger.info(
        "forgot_username inline fallback user=%s wa=%s email=%s",
        user.id,
        wa_status,
        email_status,
    )
    return ForgotUsernameResponse(
        message="Matched your phone. Your login email is shown below.",
        login_email=user.email,
    )


@router.get("/google/config", response_model=GoogleConfigResponse)
def google_config() -> GoogleConfigResponse:
    """Public config so the SPA can enable the Google button."""
    client_id = (settings.google_oauth_client_id or "").strip() or None
    return GoogleConfigResponse(enabled=bool(client_id), client_id=client_id)


@router.post("/google", response_model=TokenResponse)
def google_login(
    payload: GoogleAuthRequest,
    db: Session = Depends(get_db),
) -> TokenResponse:
    """Sign in or register with Google (GIS ID token or OAuth access token)."""
    if not google_oauth_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Sign-In is not configured yet. Use email login for now.",
        )

    try:
        profile = resolve_google_profile(
            id_token_value=payload.id_token,
            access_token=payload.access_token,
        )
    except GoogleAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    email = profile["email"]
    full_name = profile["full_name"].strip() or None
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        # Random unusable password — account is Google-auth only until they set one.
        user = User(
            email=email,
            hashed_password=hash_password(secrets.token_urlsafe(32)),
            full_name=full_name,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info("google_register email=%s", email)
    elif full_name and not user.full_name:
        user.full_name = full_name
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info("google_login email=%s", email)
    else:
        logger.info("google_login email=%s", email)

    token = create_access_token(subject=user.id)
    return TokenResponse(access_token=token)
