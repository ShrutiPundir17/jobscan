"""
Stage 1 — fast embedding similarity pre-filter.

Uses stored resume + job vectors (pgvector HNSW / cosine) so search stays
milliseconds and avoids an LLM call. Stage 2 will deep-score the shortlist.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Job, Resume, User

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MatchCandidate:
    job: Job
    similarity: float  # 0.0 – 1.0 (cosine similarity)
    score: int  # 0 – 100


def resolve_resume_for_match(
    db: Session,
    user: User,
    *,
    resume_id: UUID | None = None,
) -> Resume:
    if resume_id is not None:
        resume = db.scalar(
            select(Resume).where(Resume.id == resume_id, Resume.user_id == user.id)
        )
        if resume is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found")
    else:
        resume = db.scalar(
            select(Resume)
            .where(Resume.user_id == user.id, Resume.is_primary.is_(True))
            .order_by(Resume.updated_at.desc())
        )
        if resume is None:
            resume = db.scalar(
                select(Resume)
                .where(Resume.user_id == user.id)
                .order_by(Resume.updated_at.desc())
                .limit(1)
            )
        if resume is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Upload and embed a resume before searching for matches.",
            )

    if resume.embedding is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Resume has no embedding. Call POST /resumes/{id}/embed first.",
        )
    return resume


def _location_aliases(text: str) -> set[str]:
    """Expand a preferred location into common listing spellings."""
    aliases = {text}
    lowered = text.lower().strip()
    if lowered in {"bangalore", "bengaluru"}:
        aliases.update({"bangalore", "bengaluru"})
    elif lowered in {"mumbai", "bombay"}:
        aliases.update({"mumbai", "bombay"})
    elif lowered in {"delhi", "new delhi", "ncr", "gurgaon", "gurugram", "noida"}:
        aliases.update({"delhi", "new delhi", "ncr", "gurgaon", "gurugram", "noida"})
    elif lowered in {"hyderabad", "hyd", "secunderabad"}:
        aliases.update({"hyderabad", "secunderabad"})
    elif lowered in {"pune"}:
        aliases.update({"pune"})
    elif lowered in {"chennai", "madras"}:
        aliases.update({"chennai", "madras"})
    elif lowered in {"remote", "wfh", "work from home", "anywhere"}:
        aliases.update({"remote", "wfh", "work from home", "anywhere"})
    return aliases


def location_preference_clauses(user: User) -> list:
    """
    Strict SQLAlchemy OR clauses for preferred locations.

    Only matches the cities/aliases the user listed. Remote/WFH is included
    only when the user explicitly prefers Remote. Jobs with missing location
    are NOT treated as matches (they were leaking Bangalore/other cities).
    """
    locations = user.preferred_locations or []
    if not isinstance(locations, list) or not locations:
        return []

    clauses = []
    wants_remote = False
    # JSONB text extract: raw_payload->>'search_location'
    search_loc = Job.raw_payload["search_location"].as_string()
    for loc in locations:
        text = str(loc).strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in {"remote", "wfh", "work from home", "anywhere"}:
            wants_remote = True
        for alias in _location_aliases(text):
            clauses.append(Job.location.ilike(f"%{alias}%"))
            # Jobs scraped for this city even if card location was blank.
            clauses.append(search_loc.ilike(f"%{alias}%"))

    if wants_remote:
        clauses.extend(
            [
                Job.location.ilike("%remote%"),
                Job.location.ilike("%work from home%"),
                Job.location.ilike("%wfh%"),
                Job.location.ilike("%anywhere%"),
            ]
        )

    return clauses


def _apply_preference_filters(stmt: Select, user: User) -> Select:
    """Filter jobs to the user's preferred locations (+ remote)."""
    clauses = location_preference_clauses(user)
    if not clauses:
        return stmt
    return stmt.where(or_(*clauses))


def search_similar_jobs(
    db: Session,
    resume: Resume,
    user: User,
    *,
    limit: int | None = None,
    min_similarity: float | None = None,
    apply_location_prefs: bool = True,
) -> list[MatchCandidate]:
    """
    Rank jobs by cosine similarity to the resume embedding.

    Returns up to `limit` candidates with similarity >= `min_similarity`.
    """
    if resume.embedding is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Resume has no embedding.",
        )

    top_k = max(1, min(limit or settings.match_vector_limit, 200))
    floor = (
        min_similarity
        if min_similarity is not None
        else float(settings.match_min_similarity)
    )
    floor = max(0.0, min(1.0, floor))

    vector = list(resume.embedding)
    distance = Job.embedding.cosine_distance(vector)
    similarity_expr = (1 - distance).label("similarity")

    stmt = (
        select(Job, similarity_expr)
        .where(Job.embedding.is_not(None))
        .order_by(distance)
        .limit(top_k)
    )
    if apply_location_prefs:
        stmt = _apply_preference_filters(stmt, user)

    rows = db.execute(stmt).all()
    candidates: list[MatchCandidate] = []
    for job, similarity in rows:
        sim = float(similarity or 0.0)
        if sim < floor:
            continue
        score = max(0, min(100, int(round(sim * 100))))
        candidates.append(MatchCandidate(job=job, similarity=sim, score=score))

    logger.info(
        "match_search user=%s resume=%s candidates=%s limit=%s floor=%.2f",
        user.id,
        resume.id,
        len(candidates),
        top_k,
        floor,
    )
    return candidates
