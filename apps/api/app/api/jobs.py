from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_user
from app.api.stubs import not_implemented
from app.models import User

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
def list_jobs(
    current_user: User = Depends(get_current_user),
    q: str | None = Query(default=None, description="Search title/company"),
    source: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """List scraped jobs (optionally filtered)."""
    return not_implemented(
        "jobs.list",
        user_id=str(current_user.id),
        q=q,
        source=source,
        limit=limit,
        offset=offset,
    )


@router.get("/{job_id}")
def get_job(job_id: UUID, current_user: User = Depends(get_current_user)):
    """Get a single job posting."""
    return not_implemented(
        "jobs.get",
        user_id=str(current_user.id),
        job_id=str(job_id),
    )
