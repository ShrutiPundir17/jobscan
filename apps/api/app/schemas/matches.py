from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class MatchSearchRequest(BaseModel):
    resume_id: UUID | None = None
    limit: int | None = Field(default=None, ge=1, le=200)
    min_similarity: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Cosine similarity floor (0–1). Defaults to MATCH_MIN_SIMILARITY.",
    )
    apply_location_prefs: bool = True


class MatchScoreRequest(BaseModel):
    resume_id: UUID | None = None
    limit: int | None = Field(
        default=None,
        ge=1,
        le=20,
        description="Max Stage 1 candidates to deep-score. Defaults to MATCH_DEEP_SCORE_LIMIT.",
    )
    min_similarity: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Stage 1 cosine floor (0–1).",
    )
    apply_location_prefs: bool = True
    persist: bool = Field(
        default=True,
        description="Upsert applications when LLM score >= min_match_score.",
    )
    min_match_score: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Persist threshold. Defaults to the user's min_match_score.",
    )


class MatchJobSummary(BaseModel):
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

    model_config = {"from_attributes": True}


class MatchCandidateResponse(BaseModel):
    job: MatchJobSummary
    similarity: float = Field(description="Cosine similarity 0–1")
    score: int = Field(description="Rounded similarity as 0–100")
    stage: str = "vector"


class MatchSearchResponse(BaseModel):
    stage: str = "vector"
    resume_id: UUID
    count: int
    candidates: list[MatchCandidateResponse]


class MatchDeepScoreResponse(BaseModel):
    job: MatchJobSummary
    similarity: float = Field(description="Stage 1 cosine similarity 0–1")
    vector_score: int = Field(description="Stage 1 score 0–100")
    score: int = Field(description="LLM match score 0–100")
    verdict: str = Field(description="strong | good | partial | weak")
    reasoning: str
    skill_gaps: list[str] = Field(default_factory=list)
    tailored_pitch: str
    stage: str = "llm"
    persisted: bool = False
    application_id: UUID | None = None
    status: str | None = None


class MatchScoreResponse(BaseModel):
    stage: str = "llm"
    resume_id: UUID
    count: int
    persisted_count: int
    min_match_score: int
    results: list[MatchDeepScoreResponse]


class PersistedMatchResponse(BaseModel):
    id: UUID
    status: str
    match_score: int | None = None
    verdict: str | None = Field(
        default=None,
        description="strong | good | partial | weak",
    )
    match_reasoning: str | None = None
    skill_gaps: list[str] = Field(default_factory=list)
    tailored_pitch: str | None = None
    tailored_bullets: dict | None = None
    tailored_resume_text: str | None = None
    resume_id: UUID | None = None
    job: MatchJobSummary
    created_at: datetime
    updated_at: datetime


class PersistedMatchListResponse(BaseModel):
    items: list[PersistedMatchResponse]
    total: int
    limit: int
    offset: int


class TailorResumeResponse(BaseModel):
    application_id: UUID
    job: MatchJobSummary
    tailored_bullets: dict
    tailored_resume_text: str
    tailored_pitch: str | None = None
