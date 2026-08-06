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
    """Manually enqueue a job-portal scan (same task as the 2-hour schedule)."""
    async_result = scan_jobs.delay()
    return TriggerResponse(
        status="queued",
        task_id=async_result.id,
        message=f"Scan queued for {current_user.email}. Normally runs every 2 hours via Celery beat.",
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
