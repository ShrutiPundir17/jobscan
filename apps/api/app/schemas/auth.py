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
