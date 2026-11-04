"""
Auto-apply: fill public ATS forms for a match, wait for approval, then submit.

State machine on `applications.auto_apply_state`:

    None/dismissed ─prepare─▶ queued ─▶ preparing ─┬▶ awaiting_approval ─approve─▶ submitting ─▶ sending ─┬▶ submitted
                                                   ├▶ needs_manual                                         ├▶ unconfirmed ─resolve─▶ submitted | failed
                                                   ├▶ unsupported                                          └▶ failed
                                                   └▶ failed
    discard ─▶ dismissed (never auto-queued again)

Invariants
- Nothing is submitted without an explicit approve.
- Every queued task carries `auto_apply_attempt`; a worker may only act after an
  atomic compare-and-swap on (state, attempt). Late, duplicate or superseded
  tasks therefore no-op — one approval can never produce two submissions.
- Once the submit button may have fired, the outcome is `submitted` or
  `unconfirmed`; `unconfirmed` is never retried until the user says it wasn't
  received.
- Submit sends the approved snapshot, not live profile data.
- Busy states that outlive their worker are *read* as failed (or unconfirmed
  for `sending`), so the UI can always recover.
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import requests
from fastapi import HTTPException, status
from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.db import SessionLocal
from app.models import Application, ApplicationStatus, Job, Notification, Resume, User

logger = logging.getLogger(__name__)

QUEUED = "queued"
PREPARING = "preparing"
AWAITING_APPROVAL = "awaiting_approval"
NEEDS_MANUAL = "needs_manual"
UNSUPPORTED = "unsupported"
SUBMITTING = "submitting"  # approved, waiting for a worker
SENDING = "sending"  # a worker owns it; the click may happen at any moment
SUBMITTED = "submitted"
UNCONFIRMED = "unconfirmed"
FAILED = "failed"
DISMISSED = "dismissed"

# How long a busy state may last before we assume its worker is gone.
STALE_AFTER = {
    QUEUED: timedelta(minutes=60),  # may wait behind an hourly portal scan
    PREPARING: timedelta(minutes=15),
    SUBMITTING: timedelta(minutes=60),
    SENDING: timedelta(minutes=15),
}
_STALE_ERRORS = {
    QUEUED: "The agent never started (worker busy or restarted). Try again.",
    PREPARING: "The agent stopped responding while filling the form. Try again.",
    SUBMITTING: "The approved submission never started — nothing was sent. Prepare again to retry.",
    SENDING: (
        "The agent stopped while submitting — it may or may not have been received. "
        "Check your email or the employer's site, then tell us."
    ),
}
PLAN_MAX_AGE = timedelta(days=7)

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


class EnqueueError(RuntimeError):
    pass


# --- state helpers -------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _is_stale(app: Application) -> bool:
    limit = STALE_AFTER.get(app.auto_apply_state or "")
    if limit is None:
        return False
    updated = _aware(app.auto_apply_updated_at)
    return updated is None or _now() - updated >= limit


def effective_state(app: Application) -> str | None:
    """The state the user should see and that actions are judged against."""
    state = app.auto_apply_state
    if state in STALE_AFTER and _is_stale(app):
        return UNCONFIRMED if state == SENDING else FAILED
    return state


def effective_error(app: Application) -> str | None:
    state = app.auto_apply_state
    if state in STALE_AFTER and _is_stale(app):
        return _STALE_ERRORS[state]
    return app.auto_apply_error


def is_working(app: Application) -> bool:
    return effective_state(app) in STALE_AFTER


def _cas(db: Session, app: Application, *, expect_state: str | None, expect_attempt: str | None, **values) -> bool:
    """Atomically move the row only if nobody else changed (state, attempt) meanwhile."""
    values.setdefault("auto_apply_updated_at", _now())
    result = db.execute(
        update(Application)
        .where(
            Application.id == app.id,
            Application.auto_apply_state.is_(None)
            if expect_state is None
            else Application.auto_apply_state == expect_state,
            Application.auto_apply_attempt.is_(None)
            if expect_attempt is None
            else Application.auto_apply_attempt == expect_attempt,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.expire(app)
    return result.rowcount == 1


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def usage_24h(db: Session, user_id: UUID, *, exclude_id: UUID | None = None) -> int:
    """Submissions that count toward the daily cap, including ones that may have been sent."""
    since = _now() - timedelta(hours=24)
    filters = [
        Application.user_id == user_id,
        or_(
            and_(Application.auto_apply_state == SUBMITTED, Application.applied_at >= since),
            and_(
                Application.auto_apply_state.in_([SENDING, UNCONFIRMED]),
                Application.auto_apply_updated_at >= since,
            ),
        ),
    ]
    if exclude_id is not None:
        filters.append(Application.id != exclude_id)
    return int(db.scalar(select(func.count()).select_from(Application).where(*filters)) or 0)


def _pending_submits(db: Session, user_id: UUID) -> int:
    fresh = _now() - STALE_AFTER[SUBMITTING]
    return int(
        db.scalar(
            select(func.count())
            .select_from(Application)
            .where(
                Application.user_id == user_id,
                Application.auto_apply_state == SUBMITTING,
                Application.auto_apply_updated_at >= fresh,
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


def _enqueue(task_name: str, app_id: UUID, attempt: str) -> None:
    from app.tasks import auto_apply as tasks

    try:
        getattr(tasks, task_name).delay(str(app_id), attempt)
    except Exception as exc:  # noqa: BLE001 — broker down
        logger.exception("auto_apply enqueue failed task=%s app=%s", task_name, app_id)
        raise EnqueueError(str(exc)) from exc


_UNAVAILABLE = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="Could not reach the auto-apply worker. Try again shortly.",
)


# --- API-side actions ----------------------------------------------------


def request_prepare(db: Session, app: Application, *, automatic: bool = False) -> Application:
    _require_platform_enabled()
    eff = effective_state(app)
    if app.status in CLOSED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot auto-apply from status '{app.status.value}'.",
        )
    if eff == SUBMITTED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This application was already submitted.")
    if eff == UNCONFIRMED:
        raise _conflict("A submission may already have been received. Tell us whether it arrived first.")
    if eff in STALE_AFTER:
        raise _conflict("Auto-apply is already working on this application.")

    before = {
        "auto_apply_state": app.auto_apply_state,
        "auto_apply_attempt": app.auto_apply_attempt,
        "auto_apply_plan": app.auto_apply_plan,
        "auto_apply_error": app.auto_apply_error,
    }
    attempt = str(uuid.uuid4())
    if not _cas(
        db,
        app,
        expect_state=before["auto_apply_state"],
        expect_attempt=before["auto_apply_attempt"],
        auto_apply_state=QUEUED,
        auto_apply_attempt=attempt,
        auto_apply_plan=None,
        auto_apply_error=None,
    ):
        raise _conflict("Auto-apply state changed — refresh and try again.")
    try:
        _enqueue("prepare_auto_apply", app.id, attempt)
    except EnqueueError as exc:
        if automatic:
            # Put it back exactly as it was so the next match run retries it.
            _cas(db, app, expect_state=QUEUED, expect_attempt=attempt, **before)
        else:
            _cas(
                db,
                app,
                expect_state=QUEUED,
                expect_attempt=attempt,
                auto_apply_state=FAILED,
                auto_apply_error="Could not reach the auto-apply worker. Try again shortly.",
            )
        raise _UNAVAILABLE from exc
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
    if effective_state(app) != AWAITING_APPROVAL or not plan or not plan.get("ready") or not plan.get("applicant"):
        raise _conflict("Nothing is waiting for approval. Prepare auto-apply first.")
    prepared_at = _parse_dt(plan.get("prepared_at"))
    if prepared_at is None or _now() - prepared_at > PLAN_MAX_AGE:
        raise _conflict("This plan is more than a week old. Prepare again so we check the form is still open.")
    limit = settings.auto_apply_daily_limit
    if usage_24h(db, app.user_id) + _pending_submits(db, app.user_id) >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Daily auto-apply limit reached ({limit} in 24h). Try again later or apply manually.",
        )

    previous_attempt = app.auto_apply_attempt
    attempt = str(uuid.uuid4())
    approved_plan = {**plan, "approved_at": _now().isoformat()}
    if not _cas(
        db,
        app,
        expect_state=AWAITING_APPROVAL,
        expect_attempt=previous_attempt,
        auto_apply_state=SUBMITTING,
        auto_apply_attempt=attempt,
        auto_apply_plan=approved_plan,
        auto_apply_error=None,
    ):
        raise _conflict("This application is already being submitted.")
    try:
        _enqueue("submit_auto_apply", app.id, attempt)
    except EnqueueError as exc:
        # Nothing was sent; hand the approval back to the user.
        _cas(
            db,
            app,
            expect_state=SUBMITTING,
            expect_attempt=attempt,
            auto_apply_state=AWAITING_APPROVAL,
            auto_apply_attempt=previous_attempt,
            auto_apply_plan=plan,
        )
        raise _UNAVAILABLE from exc
    db.refresh(app)
    return app


def cancel(db: Session, app: Application) -> Application:
    eff = effective_state(app)
    if eff == SUBMITTED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This application was already submitted.")
    if eff in (SUBMITTING, SENDING):
        raise _conflict("Already submitting — it can't be cancelled now.")
    if eff == UNCONFIRMED:
        raise _conflict("A submission may already have been received. Tell us whether it arrived instead.")
    if eff in (None, DISMISSED):
        return app
    if not _cas(
        db,
        app,
        expect_state=app.auto_apply_state,
        expect_attempt=app.auto_apply_attempt,
        auto_apply_state=DISMISSED,
        auto_apply_attempt=None,
        auto_apply_plan=None,
        auto_apply_provider=None,
        auto_apply_url=None,
        auto_apply_error=None,
    ):
        raise _conflict("Auto-apply state changed — refresh and try again.")
    db.refresh(app)
    return app


def resolve_unconfirmed(db: Session, app: Application, *, received: bool) -> Application:
    """The user tells us whether an unconfirmed submission reached the employer."""
    if effective_state(app) != UNCONFIRMED:
        raise _conflict("There is no unconfirmed submission to resolve.")
    now = _now()
    values: dict[str, Any] = (
        {
            "auto_apply_state": SUBMITTED,
            "auto_apply_error": None,
            "status": ApplicationStatus.applied,
            "applied_at": app.applied_at or now,
        }
        if received
        else {
            "auto_apply_state": FAILED,
            "auto_apply_error": "You confirmed it wasn't received. Prepare again to retry.",
        }
    )
    if not _cas(db, app, expect_state=app.auto_apply_state, expect_attempt=app.auto_apply_attempt, **values):
        raise _conflict("Auto-apply state changed — refresh and try again.")
    db.refresh(app)
    return app


def guard_manual_change(db: Session, app: Application) -> None:
    """
    Called before a user manually applies / moves / withdraws an application.

    Blocks while a submission is in flight. Otherwise retires any pending plan
    (so an old approval can never be submitted after the user handled it by
    hand) and marks it dismissed so matching won't re-queue it.
    """
    eff = effective_state(app)
    if eff in (SUBMITTING, SENDING):
        raise _conflict("Auto-apply is submitting this application right now. Try again in a minute.")
    if eff in (None, DISMISSED, SUBMITTED, UNCONFIRMED):
        return
    if not _cas(
        db,
        app,
        expect_state=app.auto_apply_state,
        expect_attempt=app.auto_apply_attempt,
        auto_apply_state=DISMISSED,
        auto_apply_attempt=None,
        auto_apply_plan=None,
        auto_apply_error=None,
    ):
        raise _conflict("Auto-apply state changed — refresh and try again.")
    db.refresh(app)


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
            request_prepare(db, app, automatic=True)
            queued += 1
        except HTTPException as exc:
            logger.warning("auto_prepare skipped app=%s detail=%s", app.id, exc.detail)
            if exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
                break  # broker is down; rows stay untouched for the next run
    return queued


# --- worker side ---------------------------------------------------------


def resolve_target(job: Job):
    """Find the Greenhouse/Lever/Ashby form for *this* job, or None."""
    from apply_agent import detect_ats, linkedin_apply_target, unique_ats_target

    target = detect_ats(job.url)
    if target:
        return target
    if job.raw_payload:
        target = unique_ats_target(json.dumps(job.raw_payload, default=str))
        if target:
            return target
    target = unique_ats_target(job.description)
    if target or not settings.auto_apply_resolve_via_fetch:
        return target

    if not _is_fetch_host(job.url):
        return None
    try:
        resp = requests.get(job.url, headers=_FETCH_HEADERS, timeout=15)
    except requests.RequestException as exc:
        logger.warning("auto_apply fetch failed job=%s err=%s", job.id, exc)
        return None
    if resp.status_code >= 400 or not _is_fetch_host(resp.url or job.url):
        return None
    return linkedin_apply_target(resp.text)


def _is_fetch_host(url: str | None) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in _FETCH_HOSTS)


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return _aware(datetime.fromisoformat(value))
    except ValueError:
        return None


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


def applicant_snapshot(db: Session, app: Application) -> dict[str, Any]:
    """Everything the form will receive, frozen at prepare time."""
    user = app.user
    resume = _primary_resume(db, app)
    parsed = resume.parsed_data if resume is not None and isinstance(resume.parsed_data, dict) else {}
    full_name = (user.full_name or parsed.get("full_name") or "").strip()
    parts = full_name.split()
    return {
        "first_name": parts[0] if parts else "",
        "last_name": " ".join(parts[1:]),
        "email": user.email,
        "phone": (user.phone or parsed.get("phone") or "").strip() or None,
        "linkedin_url": (user.linkedin_url or "").strip() or None,
        "cover_letter": (app.match_pitch or "").strip() or None,
        "resume_id": str(resume.id) if resume is not None else None,
    }


def profile_from_snapshot(db: Session, snapshot: dict[str, Any], workdir: Path) -> tuple[Any, str]:
    from apply_agent import ApplicantProfile

    resume = None
    if snapshot.get("resume_id"):
        resume = db.get(Resume, UUID(snapshot["resume_id"]))
    path, filename, source = materialize_resume(resume, workdir)
    profile = ApplicantProfile(
        first_name=snapshot.get("first_name") or "",
        last_name=snapshot.get("last_name") or "",
        email=snapshot.get("email") or "",
        phone=snapshot.get("phone"),
        linkedin_url=snapshot.get("linkedin_url"),
        resume_path=path,
        resume_filename=filename,
        cover_letter=snapshot.get("cover_letter"),
    )
    return profile, source


def _agent():
    from apply_agent import ApplyAgent

    return ApplyAgent(
        headless=settings.auto_apply_headless,
        browser_channel=settings.auto_apply_browser_channel or None,
    )


def _load(db: Session, application_id: UUID) -> Application | None:
    return db.scalar(
        select(Application)
        .options(joinedload(Application.job), joinedload(Application.user))
        .where(Application.id == application_id)
    )


def _notify(db: Session, app: Application, kind: str, title: str, body: str) -> None:
    """In-app notification; failures never affect the auto-apply outcome."""
    try:
        db.add(
            Notification(
                user_id=app.user_id,
                type=kind,
                title=title[:255],
                body=body,
                payload={"application_id": str(app.id), "job_id": str(app.job_id)},
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("auto_apply notify failed app=%s", app.id)


def run_prepare(application_id: str, attempt: str) -> dict[str, Any]:
    from apply_agent import ApplyAgentError, not_ready_reason, title_overlaps

    db = SessionLocal()
    try:
        app = _load(db, UUID(application_id))
        if app is None or not _cas(
            db, app, expect_state=QUEUED, expect_attempt=attempt, auto_apply_state=PREPARING
        ):
            return {"status": "skipped"}

        def finish(state: str, **values) -> dict[str, Any]:
            ok = _cas(db, app, expect_state=PREPARING, expect_attempt=attempt, auto_apply_state=state, **values)
            return {"status": state if ok else "superseded"}

        try:
            target = resolve_target(app.job)
            if target is None:
                return finish(
                    UNSUPPORTED,
                    auto_apply_error=(
                        "No Greenhouse, Lever or Ashby application form found for this job. "
                        f"Apply on {app.job.source} instead."
                    ),
                )
            snapshot = applicant_snapshot(db, app)
            with tempfile.TemporaryDirectory(prefix="jobagent-apply-") as tmp:
                profile, resume_source = profile_from_snapshot(db, snapshot, Path(tmp))
                report = asyncio.run(_agent().prepare(target.apply_url, target.provider, profile))
        except ApplyAgentError as exc:
            return finish(FAILED, auto_apply_error=str(exc))

        plan = report.to_dict()
        plan.update(
            applicant=snapshot,
            resume_id=snapshot["resume_id"],
            resume_source=resume_source,
            cover_letter=snapshot["cover_letter"],
            job_title=app.job.title,
            title_match=title_overlaps(app.job.title, report.form_title),
            prepared_at=_now().isoformat(),
        )
        outcome = finish(
            AWAITING_APPROVAL if report.ready else NEEDS_MANUAL,
            auto_apply_provider=target.provider,
            auto_apply_url=target.apply_url,
            auto_apply_plan=plan,
            auto_apply_error=None if report.ready else not_ready_reason(report),
        )
        if outcome["status"] == AWAITING_APPROVAL:
            _notify(
                db,
                app,
                "auto_apply_ready",
                f"Ready to send: {app.job.title} @ {app.job.company}",
                "Your application form is filled. Review it in Applications and approve to send.",
            )
        return outcome
    except Exception as exc:  # noqa: BLE001 — prepare never submits, so failing is always safe
        logger.exception("auto_apply prepare crashed app=%s", application_id)
        db.rollback()
        app = db.get(Application, UUID(application_id))
        if app is not None:
            _cas(
                db,
                app,
                expect_state=PREPARING,
                expect_attempt=attempt,
                auto_apply_state=FAILED,
                auto_apply_error=f"Auto-apply agent error: {exc}",
            )
        return {"status": FAILED, "error": str(exc)}
    finally:
        db.close()


def _claim_for_sending(db: Session, app: Application, attempt: str) -> str:
    """
    SUBMITTING -> SENDING under a per-user lock so the daily cap can't be
    exceeded by parallel workers. Returns "claimed" | "skipped" | "limit".
    """
    try:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"auto_apply:{app.user_id}"})
        if usage_24h(db, app.user_id, exclude_id=app.id) >= settings.auto_apply_daily_limit:
            outcome = "limit"
            values = {
                "auto_apply_state": FAILED,
                "auto_apply_error": "Daily auto-apply limit reached — not submitted. Prepare again later.",
            }
        else:
            outcome = "claimed"
            values = {"auto_apply_state": SENDING}
        result = db.execute(
            update(Application)
            .where(
                Application.id == app.id,
                Application.auto_apply_state == SUBMITTING,
                Application.auto_apply_attempt == attempt,
                # Past this the UI already told the user nothing was sent — keep that true.
                Application.auto_apply_updated_at >= _now() - STALE_AFTER[SUBMITTING],
            )
            .values(auto_apply_updated_at=_now(), **values)
            .execution_options(synchronize_session=False)
        )
        db.commit()  # releases the advisory lock
    except Exception:
        db.rollback()
        raise
    db.expire(app)
    return outcome if result.rowcount == 1 else "skipped"


def run_submit(application_id: str, attempt: str) -> dict[str, Any]:
    from apply_agent import ApplyAgentError

    db = SessionLocal()
    agent_started = False
    app: Application | None = None
    try:
        app = _load(db, UUID(application_id))
        if app is None:
            return {"status": "skipped"}
        claim = _claim_for_sending(db, app, attempt)
        if claim != "claimed":
            return {"status": FAILED if claim == "limit" else "skipped"}

        def finish(state: str, **values) -> dict[str, Any]:
            ok = _cas(db, app, expect_state=SENDING, expect_attempt=attempt, auto_apply_state=state, **values)
            return {"status": state if ok else "superseded", "error": values.get("auto_apply_error")}

        plan = app.auto_apply_plan if isinstance(app.auto_apply_plan, dict) else {}
        snapshot = plan.get("applicant") if isinstance(plan.get("applicant"), dict) else None
        if app.status in CLOSED_STATUSES:
            return finish(FAILED, auto_apply_error=f"Application is '{app.status.value}' — not submitted.")
        if not snapshot or not plan.get("ready") or not app.auto_apply_url or not app.auto_apply_provider:
            return finish(FAILED, auto_apply_error="No approved plan to submit. Prepare again.")

        approved = {f["key"]: f["value"] for f in plan.get("filled", []) if isinstance(f, dict) and "key" in f}
        with tempfile.TemporaryDirectory(prefix="jobagent-apply-") as tmp:
            profile, _ = profile_from_snapshot(db, snapshot, Path(tmp))
            if not profile.resume_path:
                return finish(FAILED, auto_apply_error="The approved resume no longer exists. Prepare again.")
            agent_started = True
            try:
                result = asyncio.run(
                    _agent().submit(app.auto_apply_url, app.auto_apply_provider, profile, expected=approved)
                )
            except ApplyAgentError as exc:  # raised only before any click
                return finish(FAILED, auto_apply_error=str(exc))

        job = app.job
        if result.submitted:
            now = _now()
            outcome = finish(
                SUBMITTED,
                status=ApplicationStatus.applied,
                applied_at=now,
                auto_apply_error=None,
                auto_apply_plan={**plan, "submitted_at": now.isoformat(), "confirmation": result.confirmation},
            )
            if outcome["status"] == SUBMITTED:
                _notify(db, app, "auto_apply_submitted", f"Applied: {job.title} @ {job.company}",
                        f"The employer confirmed your application ({result.confirmation}).")
            return outcome
        if result.maybe_sent:
            outcome = finish(UNCONFIRMED, auto_apply_error=result.error)
            if outcome["status"] == UNCONFIRMED:
                _notify(db, app, "auto_apply_unconfirmed", f"Check your application: {job.title} @ {job.company}",
                        "We submitted it but couldn't see a confirmation. Check your email, then tell us in Applications.")
            return outcome
        outcome = finish(FAILED, auto_apply_error=result.error)
        if outcome["status"] == FAILED:
            _notify(db, app, "auto_apply_failed", f"Not sent: {job.title} @ {job.company}",
                    result.error or "The submission did not go through. Nothing was sent.")
        return outcome
    except Exception as exc:  # noqa: BLE001
        logger.exception("auto_apply submit crashed app=%s", application_id)
        db.rollback()
        if app is not None:
            fresh = db.get(Application, app.id)
            if fresh is not None:
                # If the browser ran, the click may have fired: never claim it's safe to retry.
                _cas(
                    db,
                    fresh,
                    expect_state=SENDING,
                    expect_attempt=attempt,
                    auto_apply_state=UNCONFIRMED if agent_started else FAILED,
                    auto_apply_error=(
                        f"Auto-apply agent error after starting to submit ({exc}). It may or may not have been received."
                        if agent_started
                        else f"Auto-apply agent error: {exc}"
                    ),
                )
        return {"status": UNCONFIRMED if agent_started else FAILED, "error": str(exc)}
    finally:
        db.close()
