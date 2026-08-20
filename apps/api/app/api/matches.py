from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db import get_db
from app.models import ApplicationStatus, User
from app.schemas.matches import (
    MatchCandidateResponse,
    MatchDeepScoreResponse,
    MatchJobSummary,
    MatchScoreRequest,
    MatchScoreResponse,
    MatchSearchRequest,
    MatchSearchResponse,
    PersistedMatchListResponse,
    PersistedMatchResponse,
    TailorResumeResponse,
)
from app.services.match_persist import (
    get_persisted_match,
    list_persisted_matches,
    serialize_skill_gaps,
)
from app.services.match_score import deep_score_matches
from app.services.match_search import resolve_resume_for_match, search_similar_jobs
from app.services.resume_tailor import tailor_application_resume

router = APIRouter(prefix="/matches", tags=["matches"])


def _to_persisted_response(app) -> PersistedMatchResponse:
    bullets = app.tailored_bullets if isinstance(app.tailored_bullets, dict) else None
    job = MatchJobSummary.model_validate(app.job)
    if job.description and len(job.description) > 3500:
        job = job.model_copy(update={"description": job.description[:3500].rstrip() + "…"})
    return PersistedMatchResponse(
        id=app.id,
        status=app.status.value,
        match_score=app.match_score,
        verdict=app.match_verdict,
        match_reasoning=app.match_reasoning,
        skill_gaps=serialize_skill_gaps(app),
        tailored_pitch=app.match_pitch,
        tailored_bullets=bullets,
        tailored_resume_text=app.tailored_resume_text,
        resume_id=app.resume_id,
        job=job,
        created_at=app.created_at,
        updated_at=app.updated_at,
    )


@router.post("/search", response_model=MatchSearchResponse)
def search_matches(
    body: MatchSearchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MatchSearchResponse:
    """
    Stage 1 — embedding similarity search.

    Returns the top job candidates by cosine similarity to the resume vector.
    No LLM scoring (use POST /matches/score for Stage 2).
    """
    resume = resolve_resume_for_match(db, current_user, resume_id=body.resume_id)
    candidates = search_similar_jobs(
        db,
        resume,
        current_user,
        limit=body.limit,
        min_similarity=body.min_similarity,
        apply_location_prefs=body.apply_location_prefs,
    )
    return MatchSearchResponse(
        stage="vector",
        resume_id=resume.id,
        count=len(candidates),
        candidates=[
            MatchCandidateResponse(
                job=MatchJobSummary.model_validate(c.job),
                similarity=round(c.similarity, 4),
                score=c.score,
                stage="vector",
            )
            for c in candidates
        ],
    )


@router.post("/score", response_model=MatchScoreResponse)
def score_matches(
    body: MatchScoreRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MatchScoreResponse:
    """
    Stage 2 — Gemini deep-score on the Stage 1 shortlist.

    Returns LLM score (0–100), verdict, reasoning, skill gaps, and a tailored pitch.
    Persists applications at/above min_match_score when persist=true.
    """
    resume, threshold, results = deep_score_matches(
        db,
        current_user,
        resume_id=body.resume_id,
        limit=body.limit,
        min_similarity=body.min_similarity,
        apply_location_prefs=body.apply_location_prefs,
        persist=body.persist,
        min_match_score=body.min_match_score,
    )
    return MatchScoreResponse(
        stage="llm",
        resume_id=resume.id,
        count=len(results),
        persisted_count=sum(1 for r in results if r.persisted),
        min_match_score=threshold,
        results=[
            MatchDeepScoreResponse(
                job=MatchJobSummary.model_validate(r.job),
                similarity=round(r.similarity, 4),
                vector_score=r.vector_score,
                score=r.score,
                verdict=r.verdict or "weak",
                reasoning=r.reasoning,
                skill_gaps=r.skill_gaps,
                tailored_pitch=r.tailored_pitch,
                stage="llm",
                persisted=r.persisted,
                application_id=r.application_id,
                status=r.status.value if r.status is not None else None,
            )
            for r in results
        ],
    )


@router.get("", response_model=PersistedMatchListResponse)
def list_matches(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    min_score: int | None = Query(default=None, ge=0, le=100),
    status_filter: ApplicationStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> PersistedMatchListResponse:
    """List persisted matches for the current user — score, verdict, gaps."""
    rows, total = list_persisted_matches(
        db,
        current_user,
        min_score=min_score,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )
    return PersistedMatchListResponse(
        items=[_to_persisted_response(app) for app in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/{application_id}/tailor", response_model=TailorResumeResponse)
def tailor_match_resume(
    application_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TailorResumeResponse:
    """
    Rewrite resume bullets for this match's job description (Gemini).

    Saves tailored_bullets + tailored_resume_text on the application.
    """
    app = tailor_application_resume(db, current_user, application_id)
    return TailorResumeResponse(
        application_id=app.id,
        job=MatchJobSummary.model_validate(app.job),
        tailored_bullets=app.tailored_bullets if isinstance(app.tailored_bullets, dict) else {},
        tailored_resume_text=app.tailored_resume_text or "",
        tailored_pitch=app.match_pitch,
    )


@router.get("/{application_id}", response_model=PersistedMatchResponse)
def get_match(
    application_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PersistedMatchResponse:
    """Get one persisted match: score, verdict, gaps, pitch, tailored resume."""
    app = get_persisted_match(db, current_user, application_id)
    return _to_persisted_response(app)
