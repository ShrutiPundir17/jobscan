from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.models import User
from app.tasks.job_embeddings import embed_pending_jobs
from app.tasks.job_scanner import scan_jobs

router = APIRouter(prefix="/scanner", tags=["scanner"])


class TriggerResponse(BaseModel):
    status: str
    task_id: str
    message: str


@router.post("/trigger", response_model=TriggerResponse)
def trigger_scan(current_user: User = Depends(get_current_user)) -> TriggerResponse:
    """
    Manually enqueue a job-portal scan.

    Uses the current user's target roles + preferred locations when set,
    so Scan jobs is not stuck on the global SCANNER_LOCATIONS default (bangalore).
    """
    roles = [
        str(r).strip()
        for r in (current_user.target_roles or [])
        if str(r).strip()
    ]
    locs = [
        str(loc).strip()
        for loc in (current_user.preferred_locations or [])
        if str(loc).strip()
    ]
    async_result = scan_jobs.delay(
        keywords=roles or None,
        locations=locs or None,
    )
    where = ", ".join(locs) if locs else "default scanner locations"
    what = ", ".join(roles[:3]) if roles else "default scanner keywords"
    return TriggerResponse(
        status="queued",
        task_id=async_result.id,
        message=(
            f"Scan queued for {current_user.email} "
            f"({what} · {where}). Wait ~1–2 min, then Find matches."
        ),
    )


class EmbedJobsRequest(BaseModel):
    batch_size: int | None = Field(default=None, ge=1, le=200)


@router.post("/embed-jobs", response_model=TriggerResponse)
def trigger_embed_jobs(
    body: EmbedJobsRequest | None = None,
    current_user: User = Depends(get_current_user),
) -> TriggerResponse:
    """Manually enqueue embedding for jobs missing vectors (Stage 1)."""
    batch = body.batch_size if body else None
    async_result = embed_pending_jobs.delay(batch)
    return TriggerResponse(
        status="queued",
        task_id=async_result.id,
        message=f"Job embedding batch queued for {current_user.email}.",
    )
