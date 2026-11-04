from __future__ import annotations

import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, load_only

from app.api.deps import get_current_user
from app.db import get_db
from app.models import Application, Job, User
from app.schemas.jobs import JobDetail, JobListItem, JobListResponse, MyApplicationSummary
from app.services.match_score import verdict_from_score
from app.services.match_search import location_preference_clauses

router = APIRouter(prefix="/jobs", tags=["jobs"])

SNIPPET_CHARS = 280
MAX_TOKENS = 6

# Never select raw_payload (internal) or embedding (768 floats per row).
_LIST_COLUMNS = (
    Job.id,
    Job.source,
    Job.title,
    Job.company,
    Job.location,
    Job.url,
    Job.employment_type,
    Job.salary_min,
    Job.salary_max,
    Job.currency,
    Job.posted_at,
    Job.created_at,
    Job.description,
)


def like_pattern(term: str) -> str:
    """Escape LIKE wildcards so user input is matched literally."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def snippet(text: str | None) -> str | None:
    if not text:
        return None
    flat = re.sub(r"\s+", " ", text).strip()
    if len(flat) <= SNIPPET_CHARS:
        return flat
    cut = flat[:SNIPPET_CHARS].rsplit(" ", 1)[0]
    return cut.rstrip(" ,.;:-") + "…"


def _my_app(app: Application | None) -> MyApplicationSummary | None:
    if app is None:
        return None
    return MyApplicationSummary(
        id=app.id,
        status=app.status.value,
        match_score=app.match_score,
        verdict=app.match_verdict or verdict_from_score(app.match_score),
    )


def _item(job: Job, app: Application | None) -> dict:
    return {
        "id": job.id,
        "source": job.source,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "url": job.url,
        "employment_type": job.employment_type,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "currency": job.currency,
        "posted_at": job.posted_at,
        "created_at": job.created_at,
        "description_snippet": snippet(job.description),
        "my_application": _my_app(app),
    }


def _with_my_application(user: User):
    """Select (Job, the caller's Application or None). (user_id, job_id) is unique, so no row duplication."""
    return (
        select(Job, Application)
        .options(
            load_only(*_LIST_COLUMNS),
            load_only(Application.id, Application.status, Application.match_score, Application.match_verdict),
        )
        .outerjoin(
            Application,
            and_(Application.job_id == Job.id, Application.user_id == user.id),
        )
    )


@router.get("", response_model=JobListResponse)
def list_jobs(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    q: str | None = Query(default=None, max_length=100, description="Words to find in title, company or location"),
    source: str | None = Query(default=None, max_length=32, pattern=r"^[A-Za-z0-9_-]+$"),
    location: str | None = Query(default=None, max_length=80),
    my_locations: bool = Query(default=False, description="Only jobs in your preferred locations"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10_000),
) -> JobListResponse:
    """Browse and search scraped jobs, newest first, with your own application status on each."""
    filters = []
    tokens = (q or "").split()[:MAX_TOKENS]
    for token in tokens:
        pattern = like_pattern(token)
        filters.append(
            or_(
                Job.title.ilike(pattern, escape="\\"),
                Job.company.ilike(pattern, escape="\\"),
                Job.location.ilike(pattern, escape="\\"),
            )
        )
    if source:
        filters.append(Job.source == source.lower())
    if location and location.strip():
        filters.append(Job.location.ilike(like_pattern(location.strip()), escape="\\"))
    if my_locations:
        clauses = location_preference_clauses(current_user)
        if clauses:
            filters.append(or_(*clauses))

    total = int(db.scalar(select(func.count()).select_from(Job).where(*filters)) or 0)
    newest = func.coalesce(Job.posted_at, Job.created_at)
    rows = db.execute(
        _with_my_application(current_user)
        .where(*filters)
        .order_by(newest.desc(), Job.id.desc())  # id tie-break keeps pages stable
        .limit(limit)
        .offset(offset)
    ).all()
    return JobListResponse(
        items=[JobListItem(**_item(job, app)) for job, app in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{job_id}", response_model=JobDetail)
def get_job(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JobDetail:
    """One job with its full description and your application status."""
    row = db.execute(_with_my_application(current_user).where(Job.id == job_id)).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    job, app = row
    return JobDetail(**_item(job, app), description=job.description)
