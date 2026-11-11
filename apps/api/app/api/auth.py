import hashlib
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
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
from app.security import BCRYPT_MAX_BYTES, create_access_token, hash_password, verify_password
from app.services import rate_limit as rl
from app.services.google_auth import GoogleAuthError, google_oauth_configured, resolve_google_profile
from app.services.notify_email import send_email

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

# Unknown emails still pay for one bcrypt check, so response time doesn't
# reveal which emails have accounts.
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(16))

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


def _try_send_whatsapp(
    *,
    phone: str | None,
    body: str,
    kind: str | None = None,
    variables: list[object] | None = None,
) -> str:
    if not phone or not settings.whatsapp_configured():
        return "skipped"
    from app.services.notify_whatsapp import send_whatsapp

    return send_whatsapp(to_phone=phone, body=body, kind=kind, variables=variables)


def _token_for(user: User) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(subject=user.id, session_version=user.session_version or 0)
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(payload: UserRegister, request: Request, db: Session = Depends(get_db)) -> User:
    rl.hit((rl.REGISTER_IP, rl.client_ip(request)), message="Too many sign-ups from this network.")
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
def login(payload: UserLogin, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    email = payload.email.lower().strip()
    ip = rl.client_ip(request)
    limits = (
        (rl.LOGIN_FAIL_PAIR, f"{email}|{ip}"),
        (rl.LOGIN_FAIL_IP, ip),
        (rl.LOGIN_FAIL_EMAIL, email),
    )
    rl.enforce(*limits, message="Too many failed sign-in attempts.")

    user = db.scalar(select(User).where(User.email == email))
    password_ok = (
        len(payload.password.encode("utf-8")) <= BCRYPT_MAX_BYTES
        and verify_password(payload.password, user.hashed_password if user else _DUMMY_PASSWORD_HASH)
    )
    if user is None or not password_ok:
        for rule, ident in limits:  # only failures count toward the limits
            rl.record(rule, ident)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    rl.clear(rl.LOGIN_FAIL_PAIR, f"{email}|{ip}")
    return _token_for(user)


@router.post("/logout-all", response_model=dict)
def logout_all(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Sign out on every device (revokes all existing tokens, including this one)."""
    current_user.session_version = (current_user.session_version or 0) + 1
    db.add(current_user)
    db.commit()
    return {"message": "Signed out on all devices."}


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> ForgotPasswordResponse:
    email = str(payload.email).lower().strip()
    # Counted before the account lookup so a 429 never reveals whether it exists.
    rl.hit(
        (rl.FORGOT_TARGET, f"email:{email}"),
        (rl.FORGOT_IP, rl.client_ip(request)),
        message="Too many password reset requests.",
    )
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

    wa_status = _try_send_whatsapp(
        phone=user.phone, body=wa_body, kind="password_reset", variables=[reset_url]
    )
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
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    rl.hit((rl.RESET_IP, rl.client_ip(request)), message="Too many reset attempts.")
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
    # Sign out everywhere: whoever had the old password loses their session too.
    user.session_version = (user.session_version or 0) + 1
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
    request: Request,
    db: Session = Depends(get_db),
) -> ForgotUsernameResponse:
    phone = payload.phone.strip()
    rl.hit(
        (rl.FORGOT_TARGET, f"phone:{_phone_digits(phone)[-10:]}"),
        (rl.FORGOT_IP, rl.client_ip(request)),
        message="Too many username recovery requests.",
    )
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
    wa_status = _try_send_whatsapp(
        phone=user.phone, body=wa_body, kind="login_email", variables=[user.email]
    )
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
    request: Request,
    db: Session = Depends(get_db),
) -> TokenResponse:
    """Sign in or register with Google (GIS ID token or OAuth access token)."""
    rl.hit((rl.GOOGLE_IP, rl.client_ip(request)), message="Too many sign-in attempts.")
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

    return _token_for(user)
