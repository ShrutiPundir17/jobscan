from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field


class ResumeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str | None
    raw_text: str | None
    parsed_data: dict | None = None
    is_primary: bool
    embedding_model: str | None = None
    embedded_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_embedding(self) -> bool:
        return self.embedded_at is not None


class ResumeListResponse(BaseModel):
    items: list[ResumeResponse]
    total: int = Field(ge=0)


class ResumeDeleteResponse(BaseModel):
    id: UUID
    new_primary_id: UUID | None = Field(
        default=None,
        description="Set when the deleted resume was primary and another one took over.",
    )
    retired_auto_apply_plans: int = Field(
        default=0,
        description="Auto-apply plans that used this resume and must be prepared again.",
    )


class EmbeddingStatusResponse(BaseModel):
    resume_id: UUID
    has_embedding: bool
    embedding_model: str | None = None
    embedded_at: datetime | None = None
    dimensions: int = 0
