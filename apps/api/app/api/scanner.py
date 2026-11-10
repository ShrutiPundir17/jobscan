from datetime import UTC, datetime, timedelta, timezone

import requests
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import settings
from app.db import get_db
from app.models import Application, Job, User
from app.services import scan_guard
from app.services.scan_status import read_scan_telemetry, record_scan_event
from app.tasks.dispatch import dispatch
from app.tasks.job_embeddings import embed_pending_jobs
from app.tasks.job_scanner import HARD_TIME_LIMIT, _parse_csv, scan_jobs

router = APIRouter(prefix="/scanner", tags=["scanner"])

IST = timezone(timedelta(hours=5, minutes=30))
# A "running"/"queued" status older than this means the worker died mid-scan.
RUNNING_STALE_AFTER = timedelta(seconds=HARD_TIME_LIMIT + 120)
QUEUED_STALE_AFTER = scan_guard.QUEUED_TTL


class TriggerResponse(BaseModel):
    status: str
    task_id: str
    message: str


class PortalHealth(BaseModel):
    name: str
    state: str = Field(description="ok | cooling_down | failing | unknown")
    cooldown_until: datetime | None = None
    last_ok_at: datetime | None = None
    last_scraped: int | None = None
    last_error: str | None = None
    last_error_kind: str | None = None


class AgentStatusResponse(BaseModel):
    state: str
    scanner_enabled: bool
    last_scan_at: datetime | None
    jobs_scanned_today: int
    high_match_count: int
    high_match_threshold: int = 85
    server_time: datetime
    last_scan_status: str | None = None
    portals: list[PortalHealth] = Field(default_factory=list)


@router.get("/status", response_model=AgentStatusResponse)
def agent_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AgentStatusResponse:
    """Live agent metrics for the dashboard status panel."""
    now_utc = datetime.now(UTC)
    start_ist = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start_ist.astimezone(UTC)

    tel = read_scan_telemetry()
    last_scan_status = tel.get("last_scan_status")
    if not isinstance(last_scan_status, str):
        last_scan_status = None

    last_scan_at: datetime | None = None
    raw_at = tel.get("last_scan_at")
    if isinstance(raw_at, str) and raw_at:
        try:
            last_scan_at = datetime.fromisoformat(raw_at)
        except ValueError:
            last_scan_at = None

    if last_scan_at is None:
        # Prefer updated_at — upserts refresh this even when no new rows are inserted.
        last_scan_at = db.scalar(select(func.max(Job.updated_at))) or db.scalar(
            select(func.max(Job.created_at))
        )

    scanned_today_redis = tel.get("scanned_today")
    jobs_touched_today = int(
        db.scalar(
            select(func.count())
            .select_from(Job)
            .where(or_(Job.created_at >= start_utc, Job.updated_at >= start_utc))
        )
        or 0
    )
    if isinstance(scanned_today_redis, int):
        jobs_scanned_today = scanned_today_redis
    else:
        jobs_scanned_today = jobs_touched_today

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

    # A worker killed mid-scan never writes its final status.
    if last_scan_at is not None and last_scan_status in {"running", "queued"}:
        age = now_utc - (last_scan_at if last_scan_at.tzinfo else last_scan_at.replace(tzinfo=UTC))
        limit = RUNNING_STALE_AFTER if last_scan_status == "running" else QUEUED_STALE_AFTER
        if age > limit:
            last_scan_status = "stalled"

    if not settings.scanner_enabled:
        state = "paused"
    elif last_scan_status in {"running", "queued"}:
        state = "active"
    elif last_scan_status in {"partial", "failed", "blocked", "stalled"}:
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
        portals=[PortalHealth(**p) for p in scan_guard.portal_health(_parse_csv(settings.scanner_portals))],
    )


@router.post("/trigger", response_model=TriggerResponse)
def trigger_scan(current_user: User = Depends(get_current_user)) -> TriggerResponse:
    """
    Manually enqueue a job-portal scan.

    Uses the current user's target roles + preferred locations when set,
    so Scan jobs is not stuck on the global SCANNER_LOCATIONS default (bangalore).
    Only one scan runs at a time and each user can trigger one per cooldown.
    """
    if not settings.scanner_enabled:
        return TriggerResponse(status="disabled", task_id="", message="Job scanning is paused on this server.")
    inline = settings.task_mode == "inline"
    if inline and not settings.github_dispatch_token:
        return TriggerResponse(
            status="scheduled",
            task_id="",
            message="Job scans run automatically every few hours — new jobs will appear after the next one.",
        )
    if scan_guard.scan_in_progress():
        return TriggerResponse(
            status="already_queued",
            task_id="",
            message="A scan is already running — new jobs will appear in a few minutes.",
        )
    cooldown = timedelta(seconds=settings.scanner_manual_cooldown_seconds)
    wait = scan_guard.manual_trigger_wait(current_user.id, cooldown)
    if wait:
        minutes = max(1, round(wait / 60))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"You scanned recently. Try again in about {minutes} min.",
            headers={"Retry-After": str(wait)},
        )

    roles = [
        str(r).strip()
        for r in (current_user.target_roles or [])
        if str(r).strip()
    ][: max(1, settings.scanner_max_keywords)]
    locs = [
        str(loc).strip()
        for loc in (current_user.preferred_locations or [])
        if str(loc).strip()
    ][: max(1, settings.scanner_max_locations)]
    scan_guard.mark_queued()
    try:
        if inline:
            # No worker here: run the scan on GitHub Actions instead.
            _dispatch_github_scan(roles, locs)
            task_id = "github-actions"
        else:
            task_id = scan_jobs.delay(
                keywords=roles or None,
                locations=locs or None,
            ).id
    except Exception as exc:  # noqa: BLE001 — broker / GitHub unreachable
        scan_guard.clear_queued()
        scan_guard.release_manual_trigger(current_user.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not reach the scanner worker. Try again shortly.",
        ) from exc
    record_scan_event(status="queued", scraped=0, bump_daily=False)
    where = ", ".join(locs) if locs else "default scanner locations"
    what = ", ".join(roles[:3]) if roles else "default scanner keywords"
    return TriggerResponse(
        status="queued",
        task_id=task_id,
        message=(
            f"Scan queued for {current_user.email} "
            f"({what} · {where}). Wait a few minutes, then Find matches."
        ),
    )


def _dispatch_github_scan(roles: list[str], locs: list[str]) -> None:
    """Start .github/workflows/scan.yml via workflow_dispatch (expects HTTP 204)."""
    url = (
        f"https://api.github.com/repos/{settings.github_repo}/actions/workflows/"
        f"{settings.github_scan_workflow}/dispatches"
    )
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {settings.github_dispatch_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={
            "ref": settings.github_scan_ref,
            "inputs": {"keywords": ",".join(roles), "locations": ",".join(locs)},
        },
        timeout=15,
    )
    if resp.status_code != 204:
        # Never log the token; GitHub's error body doesn't contain it.
        raise RuntimeError(f"GitHub workflow dispatch failed: HTTP {resp.status_code} {resp.text[:200]}")


class EmbedJobsRequest(BaseModel):
    batch_size: int | None = Field(default=None, ge=1, le=200)


@router.post("/embed-jobs", response_model=TriggerResponse)
def trigger_embed_jobs(
    body: EmbedJobsRequest | None = None,
    current_user: User = Depends(get_current_user),
) -> TriggerResponse:
    """Manually enqueue embedding for jobs missing vectors (Stage 1)."""
    batch = body.batch_size if body else None
    async_result = dispatch(embed_pending_jobs, batch)
    return TriggerResponse(
        status="queued",
        task_id=str(getattr(async_result, "id", "inline")),
        message=f"Job embedding batch queued for {current_user.email}.",
    )
