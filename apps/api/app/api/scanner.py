from datetime import UTC, datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import settings
from app.db import get_db
from app.models import Application, Job, User
from app.tasks.job_embeddings import embed_pending_jobs
from app.tasks.job_scanner import scan_jobs

router = APIRouter(prefix="/scanner", tags=["scanner"])

IST = timezone(timedelta(hours=5, minutes=30))


class TriggerResponse(BaseModel):
    status: str
    task_id: str
    message: str


class AgentStatusResponse(BaseModel):
    state: str
    scanner_enabled: bool
    last_scan_at: datetime | None
    jobs_scanned_today: int
    high_match_count: int
    high_match_threshold: int = 85
    server_time: datetime
    last_scan_status: str | None = None


@router.get("/status", response_model=AgentStatusResponse)
def agent_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AgentStatusResponse:
    """Live agent metrics for the dashboard status panel."""
    now_utc = datetime.now(UTC)
    start_ist = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start_ist.astimezone(UTC)

    # Prefer explicit scan-run telemetry from Redis; fall back to newest job row.
    last_scan_at: datetime | None = None
    last_scan_status: str | None = None
    scanned_today_redis: int | None = None
    try:
        from redis import Redis

        client = Redis.from_url(settings.redis_url)
        raw_at = client.get("jobagent:last_scan_at")
        raw_status = client.get("jobagent:last_scan_status")
        day_key = f"jobagent:scanned_day:{now_utc.strftime('%Y-%m-%d')}"
        raw_day = client.get(day_key)
        client.close()
        if raw_at:
            text = raw_at.decode() if isinstance(raw_at, bytes) else str(raw_at)
            last_scan_at = datetime.fromisoformat(text)
        if raw_status:
            last_scan_status = (
                raw_status.decode() if isinstance(raw_status, bytes) else str(raw_status)
            )
        if raw_day is not None:
            scanned_today_redis = int(
                raw_day.decode() if isinstance(raw_day, bytes) else raw_day
            )
    except Exception:  # noqa: BLE001
        last_scan_at = None

    if last_scan_at is None:
        last_scan_at = db.scalar(select(func.max(Job.created_at)))

    jobs_inserted_today = int(
        db.scalar(
            select(func.count()).select_from(Job).where(Job.created_at >= start_utc)
        )
        or 0
    )
    jobs_scanned_today = (
        scanned_today_redis if scanned_today_redis is not None else jobs_inserted_today
    )

    high_match_count = int(
        db.scalar(
            select(func.count())
            .select_from(Application)
            .where(
                Application.user_id == current_user.id,
                Application.match_score.is_not(None),
                Application.match_score >= 85,
            )
        )
        or 0
    )

    # Agent is "active" whenever scanning is enabled — the beat schedule is running.
    # "idle" only when scanning is off; "degraded" if the last run reported errors.
    if not settings.scanner_enabled:
        state = "paused"
    elif last_scan_status in {"partial", "failed"}:
        state = "degraded"
    else:
        state = "active"

    return AgentStatusResponse(
        state=state,
        scanner_enabled=settings.scanner_enabled,
        last_scan_at=last_scan_at,
        jobs_scanned_today=jobs_scanned_today,
        high_match_count=high_match_count,
        high_match_threshold=85,
        server_time=now_utc,
        last_scan_status=last_scan_status,
    )


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
