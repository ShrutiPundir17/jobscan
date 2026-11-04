from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class MyApplicationSummary(BaseModel):
    """The *current user's* application for this job, if any."""

    id: UUID
    status: str
    match_score: int | None = None
    verdict: str | None = None


class JobListItem(BaseModel):
    id: UUID
    source: str
    title: str
    company: str
    location: str | None = None
    url: str
    employment_type: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    currency: str | None = None
    posted_at: datetime | None = None
    created_at: datetime
    description_snippet: str | None = Field(default=None, description="First ~280 chars of the JD.")
    my_application: MyApplicationSummary | None = None


class JobDetail(JobListItem):
    description: str | None = None


class JobListResponse(BaseModel):
    items: list[JobListItem]
    total: int
    limit: int
    offset: int
