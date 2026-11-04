"""
Resume lifecycle: upload bookkeeping, choosing the primary resume, deleting.

Invariant: a user with at least one resume has exactly one primary resume
(DB-enforced "at most one" via uq_resumes_one_primary_per_user; "at least
one" maintained here). Every mutation takes a row lock on the user so
concurrent uploads / deletes / primary switches serialize per user.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Application, Resume, User
from app.services import auto_apply

logger = logging.getLogger(__name__)

RESUME_GONE_ERROR = "The resume used for this plan was deleted. Prepare again."


def lock_user(db: Session, user_id: UUID) -> None:
    """Serialize resume mutations for one user (held until commit/rollback)."""
    db.execute(select(User.id).where(User.id == user_id).with_for_update())


def get_owned_resume(db: Session, user_id: UUID, resume_id: UUID) -> Resume:
    resume = db.scalar(select(Resume).where(Resume.id == resume_id, Resume.user_id == user_id))
    if resume is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found")
    return resume


def _best_successor(db: Session, user_id: UUID, exclude_id: UUID) -> Resume | None:
    """Embedded resumes first (matching works immediately), then newest."""
    return db.scalar(
        select(Resume)
        .where(Resume.user_id == user_id, Resume.id != exclude_id)
        .order_by(Resume.embedded_at.is_(None), Resume.created_at.desc(), Resume.id)
        .limit(1)
    )


def set_primary(db: Session, user: User, resume_id: UUID) -> Resume:
    lock_user(db, user.id)
    resume = get_owned_resume(db, user.id, resume_id)
    if resume.is_primary:
        db.commit()
        return resume
    if resume.embedded_at is None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Parse this resume before making it primary — matching needs its embedding.",
        )
    db.execute(
        update(Resume)
        .where(Resume.user_id == user.id, Resume.is_primary.is_(True))
        .values(is_primary=False)
        .execution_options(synchronize_session=False)
    )
    resume.is_primary = True
    db.commit()
    db.refresh(resume)
    return resume


def _plans_using(db: Session, user_id: UUID, resume_id: UUID) -> list[Application]:
    return list(
        db.scalars(
            select(Application).where(
                Application.user_id == user_id,
                Application.auto_apply_plan["resume_id"].astext == str(resume_id),
            )
        ).all()
    )


def delete_resume(db: Session, user: User, resume_id: UUID) -> tuple[UUID | None, int]:
    """
    Delete a resume. Returns (new_primary_id, retired_plan_count).

    Refuses while an auto-apply submission built on this resume is in flight;
    retires plans that are waiting to use it so they can't fail later.
    """
    lock_user(db, user.id)
    resume = get_owned_resume(db, user.id, resume_id)

    plans = _plans_using(db, user.id, resume.id)
    if any(auto_apply.effective_state(a) in (auto_apply.SUBMITTING, auto_apply.SENDING) for a in plans):
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An auto-apply submission using this resume is in progress. Try again in a few minutes.",
        )

    now = datetime.now(UTC)
    retired = 0
    for app in plans:
        if auto_apply.effective_state(app) not in (auto_apply.AWAITING_APPROVAL, auto_apply.NEEDS_MANUAL):
            continue  # submitted / unconfirmed keep their history
        # Same (state, attempt) guard as the auto-apply worker, without committing
        # so the whole delete stays one transaction under the user lock.
        result = db.execute(
            update(Application)
            .where(
                Application.id == app.id,
                Application.auto_apply_state == app.auto_apply_state,
                Application.auto_apply_attempt.is_(None)
                if app.auto_apply_attempt is None
                else Application.auto_apply_attempt == app.auto_apply_attempt,
            )
            .values(
                auto_apply_state=auto_apply.FAILED,
                auto_apply_attempt=None,
                auto_apply_plan=None,
                auto_apply_error=RESUME_GONE_ERROR,
                auto_apply_updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        retired += result.rowcount

    successor = _best_successor(db, user.id, resume.id) if resume.is_primary else None
    stored_path = resume.storage_path
    db.delete(resume)
    db.flush()  # the old primary must be gone before the unique index sees a new one
    if successor is not None:
        successor.is_primary = True
    db.commit()

    remove_stored_file(stored_path)
    logger.info(
        "resume_deleted user=%s resume=%s new_primary=%s retired_plans=%s",
        user.id,
        resume_id,
        successor.id if successor else None,
        retired,
    )
    return (successor.id if successor else None), retired


def remove_stored_file(path_str: str | None) -> None:
    """Best-effort removal of an uploaded file — only ever inside UPLOAD_DIR."""
    if not path_str:
        return
    try:
        root = Path(settings.upload_dir).resolve()
        path = Path(path_str).resolve()
    except OSError:
        return
    if root not in path.parents:
        logger.warning("refusing to delete file outside upload dir: %s", path_str)
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.exception("could not delete resume file %s", path_str)
