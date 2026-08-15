from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.services.email_validation import InvalidEmailError, normalize_and_validate_email


class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)

    @field_validator("email")
    @classmethod
    def email_must_be_deliverable(cls, value: EmailStr) -> str:
        try:
            # DNS/MX check — rejects example.com and domains that cannot receive mail
            return normalize_and_validate_email(str(value), check_deliverability=True)
        except InvalidEmailError as exc:
            raise ValueError(str(exc)) from exc


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str | None
    auto_apply_enabled: bool
    min_match_score: int
    target_roles: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    created_at: datetime


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    message: str
    # Only present when outbound email is not configured (local/dev).
    reset_token: str | None = None
    reset_url: str | None = None


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str = Field(min_length=8, max_length=128)


class ForgotUsernameRequest(BaseModel):
    phone: str = Field(min_length=8, max_length=32)


class ForgotUsernameResponse(BaseModel):
    message: str
    # Only present when outbound email is not configured and a match was found.
    login_email: str | None = None


class GoogleAuthRequest(BaseModel):
    """GIS credential (ID token) and/or OAuth access token from the browser."""

    id_token: str | None = Field(default=None, max_length=4096)
    access_token: str | None = Field(default=None, max_length=4096)


class GoogleConfigResponse(BaseModel):
    enabled: bool
    client_id: str | None = None
