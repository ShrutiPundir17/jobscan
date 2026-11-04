from datetime import datetime
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class UserPreferencesUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=255)
    auto_apply_enabled: bool | None = None
    min_match_score: int | None = Field(default=None, ge=0, le=100)
    target_roles: list[str] | None = Field(default=None, max_length=30)
    preferred_locations: list[str] | None = Field(default=None, max_length=30)
    phone: str | None = Field(
        default=None,
        max_length=32,
        description="E.164 or local 10-digit (India defaulted to +91) for WhatsApp.",
    )
    notify_email_enabled: bool | None = None
    notify_whatsapp_enabled: bool | None = None
    linkedin_url: str | None = Field(
        default=None,
        max_length=512,
        description="Public LinkedIn profile URL, used when auto-apply fills forms.",
    )

    @field_validator("linkedin_url", mode="before")
    @classmethod
    def normalize_linkedin_url(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("linkedin_url must be a string")
        text = value.strip()
        if not text:
            return None
        if not text.lower().startswith(("http://", "https://")):
            text = "https://" + text
        host = (urlparse(text).hostname or "").lower()
        if host != "linkedin.com" and not host.endswith(".linkedin.com"):
            raise ValueError("Enter a linkedin.com profile URL")
        return text

    @field_validator("target_roles", "preferred_locations", mode="before")
    @classmethod
    def normalize_string_lists(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError("Must be a list of strings")
        cleaned: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("List items must be strings")
            text = " ".join(item.split()).strip()
            if text:
                cleaned.append(text[:120])
        # de-dupe while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for item in cleaned:
            key = item.lower()
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("phone must be a string")
        text = value.strip()
        if not text:
            return None
        return text[:32]


class UserPreferencesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str | None
    auto_apply_enabled: bool
    min_match_score: int
    target_roles: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    phone: str | None = None
    notify_email_enabled: bool = True
    notify_whatsapp_enabled: bool = False
    linkedin_url: str | None = None
    created_at: datetime
