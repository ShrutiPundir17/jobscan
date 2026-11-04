"""
Auto-apply: fill public ATS forms for a match, wait for approval, then submit.

State machine on `applications.auto_apply_state`:

    None ──prepare──▶ queued ─▶ preparing ─┬▶ awaiting_approval ──approve──▶ submitting ─┬▶ submitted
                                           ├▶ needs_manual   (required questions / captcha / no resume)
                                           ├▶ unsupported    (no Greenhouse/Lever/Ashby form for the job)
                                           └▶ failed                                     └▶ failed

Nothing is ever submitted without an explicit approve call from the user,
and the submit step aborts if the form would now send anything different
from the plan the user approved.
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import requests
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.db import SessionLocal
from app.models import Application, ApplicationStatus, Job, Resume, User

logger = logging.getLogger(__name__)

QUEUED = "queued"
PREPARING = "preparing"
AWAITING_APPROVAL = "awaiting_approval"
NEEDS_MANUAL = "needs_manual"
UNSUPPORTED = "unsupported"
SUBMITTING = "submitting"
SUBMITTED = "submitted"
FAILED = "failed"

BUSY_STATES = {QUEUED, PREPARING, SUBMITTING}
# A busy state older than this is treated as a crashed worker.
STALE_AFTER = timedelta(minutes=20)

CLOSED_STATUSES = {
    ApplicationStatus.applied,
    ApplicationStatus.interviewing,
    ApplicationStatus.offered,
    ApplicationStatus.rejected,
    ApplicationStatus.withdrawn,
}

_FETCH_HOSTS = ("linkedin.com",)
_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# --- state helpers -------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _set_state(app: Application, state: str | None, *, error: str | None = None) -> None:
    app.auto_apply_state = state
    app.auto_apply_error = error
    app.auto_apply_updated_at = _now()


def is_active(app: Application) -> bool:
    """True while a worker legitimately owns this application."""
    if app.auto_apply_state not in BUSY_STATES:
        return False
    updated = app.auto_apply_updated_at
    if updated is None:
        return False
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return _now() - updated < STALE_AFTER


def guard_manual_change(app: Application) -> None:
    """
    Called before a user manually applies / moves / withdraws an application.

    Blocks while a submission is in flight; otherwise drops any pending plan so
    an old approval can never be submitted after the user handled it by hand.
    """
    if app.auto_apply_state == SUBMITTING and is_active(app):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Auto-apply is submitting this application right now. Try again in a minute.",
        )
    if app.auto_apply_state not in (None, SUBMITTED):
        _set_state(app, None)
        app.auto_apply_plan = None


def submitted_last_24h(db: Session, user_id: UUID) -> int:
    since = _now() - timedelta(hours=24)
    return int(
        db.scalar(
            select(func.count())
            .select_from(Application)
            .where(
                Application.user_id == user_id,
                Application.auto_apply_state == SUBMITTED,
                Application.applied_at >= since,
            )
        )
        or 0
    )


def _require_platform_enabled() -> None:
    if not settings.auto_apply_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auto-apply is turned off on this server.",
        )


# --- API-side actions ----------------------------------------------------


def _enqueue(task_name: str, app: Application, db: Session) -> None:
    from app.tasks import auto_apply as tasks

    try:
        getattr(tasks, task_name).delay(str(app.id))
    except Exception as exc:  # noqa: BLE001 — broker down
        logger.exception("auto_apply enqueue failed task=%s app=%s", task_name, app.id)
        _set_state(app, FAILED, error="Could not queue the auto-apply agent. Try again shortly.")
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not queue the auto-apply agent. Try again shortly.",
        ) from exc


def request_prepare(db: Session, app: Application) -> Application:
    _require_platform_enabled()
    if app.status in CLOSED_STATUSES or app.auto_apply_state == SUBMITTED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot auto-apply from status '{app.status.value}'.",
        )
    if is_active(app):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Auto-apply is already working on this application.",
        )
    _set_state(app, QUEUED)
    app.auto_apply_plan = None
    db.commit()
    _enqueue("prepare_auto_apply", app, db)
    db.refresh(app)
    return app


def approve(db: Session, app: Application) -> Application:
    _require_platform_enabled()
    if app.status in CLOSED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot auto-apply from status '{app.status.value}'.",
        )
    plan = app.auto_apply_plan if isinstance(app.auto_apply_plan, dict) else None
    if app.auto_apply_state != AWAITING_APPROVAL or not plan or not plan.get("ready"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Nothing is waiting for approval. Prepare auto-apply first.",
        )
    used = submitted_last_24h(db, app.user_id)
    if used >= settings.auto_apply_daily_limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Daily auto-apply limit reached ({settings.auto_apply_daily_limit} in 24h). "
                "Try again later or apply manually."
            ),
        )
    _set_state(app, SUBMITTING)
    plan = dict(plan)
    plan["approved_at"] = _now().isoformat()
    app.auto_apply_plan = plan
    db.commit()
    _enqueue("submit_auto_apply", app, db)
    db.refresh(app)
    return app


def cancel(db: Session, app: Application) -> Application:
    if app.auto_apply_state == SUBMITTED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This application was already submitted.",
        )
    if app.auto_apply_state == SUBMITTING and is_active(app):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Already submitting — it can't be cancelled now.",
        )
    _set_state(app, None)
    app.auto_apply_plan = None
    app.auto_apply_provider = None
    app.auto_apply_url = None
    db.commit()
    db.refresh(app)
    return app


def queue_auto_prepare(db: Session, user: User, application_ids: list[str]) -> int:
    """After matching: queue prepare for fresh matches when the user opted in."""
    if not (settings.auto_apply_enabled and user.auto_apply_enabled and application_ids):
        return 0
    ids = [UUID(str(i)) for i in application_ids]
    rows = db.scalars(
        select(Application).where(
            Application.id.in_(ids),
            Application.user_id == user.id,
            Application.status == ApplicationStatus.pending_review,
            Application.auto_apply_state.is_(None),
        )
    ).all()
    queued = 0
    for app in rows:
        try:
            request_prepare(db, app)
            queued += 1
        except HTTPException as exc:
            logger.warning("auto_prepare skipped app=%s detail=%s", app.id, exc.detail)
    return queued


# --- worker side ---------------------------------------------------------


def resolve_target(job: Job):
    """Find a Greenhouse/Lever/Ashby form for this job, or None."""
    from apply_agent import first_ats_target

    payload = json.dumps(job.raw_payload, default=str) if job.raw_payload else None
    target = first_ats_target(job.url, payload, job.description)
    if target or not settings.auto_apply_resolve_via_fetch:
        return target

    host = (urlparse(job.url).hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in _FETCH_HOSTS):
        return None
    try:
        resp = requests.get(job.url, headers=_FETCH_HEADERS, timeout=15)
    except requests.RequestException as exc:
        logger.warning("auto_apply fetch failed job=%s err=%s", job.id, exc)
        return None
    if resp.status_code >= 400:
        return None
    return first_ats_target(resp.text)


def _primary_resume(db: Session, app: Application) -> Resume | None:
    if app.resume_id:
        resume = db.get(Resume, app.resume_id)
        if resume is not None:
            return resume
    return db.scalar(
        select(Resume)
        .where(Resume.user_id == app.user_id)
        .order_by(Resume.is_primary.desc(), Resume.created_at.desc())
        .limit(1)
    )


def materialize_resume(resume: Resume | None, workdir: Path) -> tuple[str | None, str | None, str]:
    """Write the resume to disk for upload. Returns (path, filename, source)."""
    if resume is None:
        return None, None, "missing"
    name = Path(resume.filename or "resume.pdf").name.replace(" ", "_") or "resume.pdf"
    if resume.file_data:
        path = workdir / name
        path.write_bytes(resume.file_data)
        return str(path), name, "original_file"
    if resume.storage_path and Path(resume.storage_path).is_file():
        return resume.storage_path, name, "original_file"
    if resume.raw_text and resume.raw_text.strip():
        txt_name = Path(name).stem + ".txt"
        path = workdir / txt_name
        path.write_text(resume.raw_text, encoding="utf-8")
        return str(path), txt_name, "text_fallback"
    return None, None, "missing"


def build_profile(db: Session, app: Application, workdir: Path) -> tuple[Any, dict[str, Any]]:
    from apply_agent import ApplicantProfile

    user = app.user
    resume = _primary_resume(db, app)
    parsed = resume.parsed_data if resume is not None and isinstance(resume.parsed_data, dict) else {}

    full_name = (user.full_name or parsed.get("full_name") or "").strip()
    parts = full_name.split()
    first, last = (parts[0], " ".join(parts[1:])) if parts else ("", "")
    phone = (user.phone or parsed.get("phone") or "").strip() or None

    path, filename, source = materialize_resume(resume, workdir)
    profile = ApplicantProfile(
        first_name=first,
        last_name=last,
        email=user.email,
        phone=phone,
        linkedin_url=(user.linkedin_url or "").strip() or None,
        resume_path=path,
        resume_filename=filename,
        cover_letter=(app.match_pitch or "").strip() or None,
    )
    meta = {
        "resume_id": str(resume.id) if resume is not None else None,
        "resume_source": source,
    }
    return profile, meta


def _agent():
    from apply_agent import ApplyAgent

    return ApplyAgent(
        headless=settings.auto_apply_headless,
        browser_channel=settings.auto_apply_browser_channel or None,
    )


def _load(db: Session, application_id: str) -> Application | None:
    return db.scalar(
        select(Application)
        .options(joinedload(Application.job), joinedload(Application.user))
        .where(Application.id == UUID(application_id))
    )


def _still(db: Session, app: Application, state: str) -> bool:
    """Re-read state — the user may have cancelled while the browser ran."""
    db.refresh(app)
    return app.auto_apply_state == state


def run_prepare(application_id: str) -> dict[str, Any]:
    from apply_agent import ApplyAgentError, not_ready_reason

    db = SessionLocal()
    try:
        app = _load(db, application_id)
        if app is None or app.auto_apply_state != QUEUED:
            return {"status": "skipped"}
        _set_state(app, PREPARING)
        db.commit()

        target = resolve_target(app.job)
        if target is None:
            _set_state(
                app,
                UNSUPPORTED,
                error=(
                    f"No Greenhouse, Lever or Ashby application form found for this job. "
                    f"Apply on {app.job.source} instead."
                ),
            )
            db.commit()
            return {"status": UNSUPPORTED}

        app.auto_apply_provider = target.provider
        app.auto_apply_url = target.apply_url
        db.commit()

        with tempfile.TemporaryDirectory(prefix="jobagent-apply-") as tmp:
            profile, meta = build_profile(db, app, Path(tmp))
            try:
                report = asyncio.run(_agent().prepare(target.apply_url, target.provider, profile))
            except ApplyAgentError as exc:
                if _still(db, app, PREPARING):
                    _set_state(app, FAILED, error=str(exc))
                    db.commit()
                return {"status": FAILED, "error": str(exc)}

        if not _still(db, app, PREPARING):
            return {"status": "cancelled"}
        plan = report.to_dict()
        plan.update(meta)
        plan["cover_letter"] = profile.cover_letter
        plan["prepared_at"] = _now().isoformat()
        app.auto_apply_plan = plan
        if report.ready:
            _set_state(app, AWAITING_APPROVAL)
        else:
            _set_state(app, NEEDS_MANUAL, error=not_ready_reason(report))
        db.commit()
        return {"status": app.auto_apply_state}
    except Exception as exc:  # noqa: BLE001
        logger.exception("auto_apply prepare crashed app=%s", application_id)
        db.rollback()
        app = _load(db, application_id)
        if app is not None and app.auto_apply_state in {QUEUED, PREPARING}:
            _set_state(app, FAILED, error=f"Auto-apply agent error: {exc}")
            db.commit()
        return {"status": FAILED, "error": str(exc)}
    finally:
        db.close()


def run_submit(application_id: str) -> dict[str, Any]:
    from apply_agent import ApplyAgentError

    db = SessionLocal()
    try:
        app = _load(db, application_id)
        if app is None or app.auto_apply_state != SUBMITTING:
            return {"status": "skipped"}
        plan = app.auto_apply_plan if isinstance(app.auto_apply_plan, dict) else {}

        def fail(message: str) -> dict[str, Any]:
            _set_state(app, FAILED, error=message)
            db.commit()
            return {"status": FAILED, "error": message}

        if app.status in CLOSED_STATUSES:
            return fail(f"Application is '{app.status.value}' — not submitting.")
        if not app.auto_apply_url or not app.auto_apply_provider or not plan.get("ready"):
            return fail("No approved plan to submit. Prepare again.")
        if submitted_last_24h(db, app.user_id) >= settings.auto_apply_daily_limit:
            return fail("Daily auto-apply limit reached. Not submitted.")

        approved = {f["key"]: f["value"] for f in plan.get("filled", []) if "key" in f}
        with tempfile.TemporaryDirectory(prefix="jobagent-apply-") as tmp:
            profile, _ = build_profile(db, app, Path(tmp))
            try:
                result = asyncio.run(
                    _agent().submit(
                        app.auto_apply_url,
                        app.auto_apply_provider,
                        profile,
                        expected=approved,
                    )
                )
            except ApplyAgentError as exc:
                return fail(str(exc))

        if not result.submitted:
            return fail(result.error or "Submission failed.")

        now = _now()
        plan = dict(plan)
        plan["submitted_at"] = now.isoformat()
        plan["confirmation"] = result.confirmation
        app.auto_apply_plan = plan
        app.status = ApplicationStatus.applied
        app.applied_at = now
        _set_state(app, SUBMITTED)
        db.commit()
        return {"status": SUBMITTED}
    except Exception as exc:  # noqa: BLE001
        logger.exception("auto_apply submit crashed app=%s", application_id)
        db.rollback()
        app = _load(db, application_id)
        if app is not None and app.auto_apply_state == SUBMITTING:
            _set_state(app, FAILED, error=f"Auto-apply agent error: {exc}")
            db.commit()
        return {"status": FAILED, "error": str(exc)}
    finally:
        db.close()
