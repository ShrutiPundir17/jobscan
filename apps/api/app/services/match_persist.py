"""
Stage 3 — read persisted matches for a user.

Each application row stores match_score, match_verdict, skill_gaps,
and reasoning from Stage 2.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models import Application, ApplicationStatus, User
from app.services.match_score import verdict_from_score


def _normalize_skill_gaps(raw: list | dict | None) -> list[str]:
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return []


def ensure_verdict(app: Application) -> str | None:
    """Prefer saved verdict; fall back to score bands for older rows."""
    if app.match_verdict:
        return app.match_verdict
    verdict = verdict_from_score(app.match_score)
    if verdict and app.match_score is not None:
        app.match_verdict = verdict
    return verdict


def list_persisted_matches(
    db: Session,
    user: User,
    *,
    min_score: int | None = None,
    status_filter: ApplicationStatus | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Application], int]:
    filters = [
        Application.user_id == user.id,
        Application.match_score.is_not(None),
    ]
    if min_score is not None:
        filters.append(Application.match_score >= int(min_score))
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
            .order_by(Application.match_score.desc(), Application.updated_at.desc())
            .limit(limit)
            .offset(offset)
        ).all()
    )
    for app in rows:
        ensure_verdict(app)
    if rows:
        db.commit()
    return rows, total


def get_persisted_match(
    db: Session,
    user: User,
    application_id: UUID,
) -> Application:
    app = db.scalar(
        select(Application)
        .options(joinedload(Application.job))
        .where(
            Application.id == application_id,
            Application.user_id == user.id,
        )
    )
    if app is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")
    if ensure_verdict(app):
        db.commit()
    return app


def serialize_skill_gaps(app: Application) -> list[str]:
    return _normalize_skill_gaps(app.skill_gaps)
