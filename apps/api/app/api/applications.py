from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user
from app.db import get_db
from app.models import Application, ApplicationStatus, User
from app.schemas.matches import MatchJobSummary

router = APIRouter(prefix="/applications", tags=["applications"])


class ApplicationUpdate(BaseModel):
    status: ApplicationStatus | None = None
    notes: str | None = Field(default=None, max_length=5000)


class ApplicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str
    match_score: int | None = None
    match_verdict: str | None = None
    match_reasoning: str | None = None
    skill_gaps: list[str] = Field(default_factory=list)
    match_pitch: str | None = None
    tailored_resume_text: str | None = None
    notes: str | None = None
    applied_at: datetime | None = None
    resume_id: UUID | None = None
    job: MatchJobSummary
    created_at: datetime
    updated_at: datetime


class ApplicationListResponse(BaseModel):
    items: list[ApplicationResponse]
    total: int
    limit: int
    offset: int


class ApplyResponse(BaseModel):
    id: UUID
    status: str
    applied_at: datetime | None = None
    job_url: str
    message: str


def _gaps(app: Application) -> list[str]:
    raw = app.skill_gaps
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def _to_response(app: Application) -> ApplicationResponse:
    return ApplicationResponse(
        id=app.id,
        status=app.status.value,
        match_score=app.match_score,
        match_verdict=app.match_verdict,
        match_reasoning=app.match_reasoning,
        skill_gaps=_gaps(app),
        match_pitch=app.match_pitch,
        tailored_resume_text=app.tailored_resume_text,
        notes=app.notes,
        applied_at=app.applied_at,
        resume_id=app.resume_id,
        job=MatchJobSummary.model_validate(app.job),
        created_at=app.created_at,
        updated_at=app.updated_at,
    )


def _get_owned_app(db: Session, user: User, application_id: UUID) -> Application:
    app = db.scalar(
        select(Application)
        .options(joinedload(Application.job))
        .where(Application.id == application_id, Application.user_id == user.id)
    )
    if app is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")
    return app


@router.get("", response_model=ApplicationListResponse)
def list_applications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    status_filter: ApplicationStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ApplicationListResponse:
    """List tracked applications for the current user."""
    filters = [Application.user_id == current_user.id]
    if status_filter is not None:
        filters.append(Application.status == status_filter)

    total = int(
        db.scalar(select(func.count()).select_from(Application).where(*filters)) or 0
    )
    rows = list(
        db.scalars(
            select(Application)
            .options(joinedload(Application.job))
            .where(*filters)
            .order_by(Application.updated_at.desc())
            .limit(limit)
            .offset(offset)
        ).all()
    )
    return ApplicationListResponse(
        items=[_to_response(a) for a in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{application_id}", response_model=ApplicationResponse)
def get_application(
    application_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApplicationResponse:
    """Get one application with job details."""
    return _to_response(_get_owned_app(db, current_user, application_id))


@router.post("/{application_id}/apply", response_model=ApplyResponse)
def apply_to_job(
    application_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApplyResponse:
    """
    Mark application as applied and return the job URL.

    Full browser auto-apply agent is not enabled yet — user completes the
    form on the employer site using the tailored resume.
    """
    app = _get_owned_app(db, current_user, application_id)
    if app.status in {ApplicationStatus.withdrawn, ApplicationStatus.rejected}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot apply from status '{app.status.value}'.",
        )
    now = datetime.now(UTC)
    app.status = ApplicationStatus.applied
    app.applied_at = now
    db.add(app)
    db.commit()
    db.refresh(app)
    return ApplyResponse(
        id=app.id,
        status=app.status.value,
        applied_at=app.applied_at,
        job_url=app.job.url,
        message="Marked as applied. Open the job URL to complete the employer application.",
    )


@router.patch("/{application_id}", response_model=ApplicationResponse)
def update_application(
    application_id: UUID,
    payload: ApplicationUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApplicationResponse:
    """Update application status or notes (dashboard tracking)."""
    app = _get_owned_app(db, current_user, application_id)
    data = payload.model_dump(exclude_unset=True)
    if "status" in data and data["status"] is not None:
        new_status = data["status"]
        app.status = new_status
        if new_status == ApplicationStatus.applied and app.applied_at is None:
            app.applied_at = datetime.now(UTC)
        if new_status != ApplicationStatus.applied:
            # Keep applied_at history even if status moves forward
            pass
    if "notes" in data:
        notes = data["notes"]
        app.notes = notes.strip() if isinstance(notes, str) and notes.strip() else None
    db.add(app)
    db.commit()
    db.refresh(app)
    return _to_response(app)


@router.post("/{application_id}/withdraw", response_model=ApplicationResponse)
def withdraw_application(
    application_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApplicationResponse:
    """Mark an application as withdrawn."""
    app = _get_owned_app(db, current_user, application_id)
    app.status = ApplicationStatus.withdrawn
    db.add(app)
    db.commit()
    db.refresh(app)
    return _to_response(app)
