"""
Stage 2 — Gemini deep-score on the Stage 1 shortlist.

Produces a 0–100 recruiter-style score with reasoning, skill gaps, and a
short tailored pitch. Strong matches can be upserted onto applications.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Application, ApplicationStatus, Job, Resume, User
from app.services.embeddings import build_job_embedding_text, build_resume_embedding_text
from app.services.gemini_client import generate_content_with_retries, require_gemini_client
from app.services.match_search import MatchCandidate, resolve_resume_for_match, search_similar_jobs

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert technical recruiter scoring candidate–job fit.

Score holistically (0–100) using:
- role/title alignment
- skills overlap vs required skills
- experience level / years
- location / remote practicality (soft signal)
- seniority mismatch (penalize juniors for senior roles and vice versa)

Be calibrated:
- 85–100: strong hire signal
- 70–84: solid match, worth applying
- 50–69: partial fit, notable gaps
- below 50: weak fit

Return ONLY valid JSON with this exact shape:
{
  "score": <integer 0-100>,
  "reasoning": "<2-4 sentences explaining the score>",
  "skill_gaps": ["<missing or weak skill>", "..."],
  "tailored_pitch": "<2-3 sentence pitch of why this candidate fits this role>"
}

skill_gaps may be an empty list when fit is strong.
Do not invent employer facts not present in the job text.
"""


def verdict_from_score(score: int | None) -> str | None:
    """Map LLM score to a saved verdict label."""
    if score is None:
        return None
    value = max(0, min(100, int(score)))
    if value >= 85:
        return "strong"
    if value >= 70:
        return "good"
    if value >= 50:
        return "partial"
    return "weak"


@dataclass
class DeepScoreResult:
    job: Job
    similarity: float
    vector_score: int
    score: int
    reasoning: str
    skill_gaps: list[str]
    tailored_pitch: str
    verdict: str | None = None
    persisted: bool = False
    application_id: UUID | None = None
    status: ApplicationStatus | None = None


def _require_gemini_client():
    return require_gemini_client(timeout_ms=60_000)


def _clip(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _build_score_prompt(resume: Resume, job: Job) -> str:
    resume_text = _clip(build_resume_embedding_text(resume), 12000)
    job_text = _clip(build_job_embedding_text(job), 10000)
    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"JOB:\n{job_text}\n\n"
        f"CANDIDATE RESUME:\n{resume_text}"
    )


def _parse_score_payload(raw: str) -> dict[str, Any]:
    content = (raw or "").strip()
    if not content:
        raise ValueError("empty LLM response")

    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            raise
        payload = json.loads(match.group(0))

    if not isinstance(payload, dict):
        raise ValueError("LLM JSON root must be an object")

    score = int(payload.get("score"))
    score = max(0, min(100, score))

    reasoning = str(payload.get("reasoning") or "").strip()
    if not reasoning:
        raise ValueError("reasoning is required")

    gaps_raw = payload.get("skill_gaps") or []
    if not isinstance(gaps_raw, list):
        raise ValueError("skill_gaps must be a list")
    skill_gaps = [str(g).strip() for g in gaps_raw if str(g).strip()][:20]

    pitch = str(payload.get("tailored_pitch") or "").strip()
    if not pitch:
        raise ValueError("tailored_pitch is required")

    return {
        "score": score,
        "reasoning": reasoning[:4000],
        "skill_gaps": skill_gaps,
        "tailored_pitch": pitch[:4000],
    }


def score_job_with_llm(resume: Resume, job: Job) -> dict[str, Any]:
    prompt = _build_score_prompt(resume, job)
    try:
        response = generate_content_with_retries(
            contents=prompt,
            config={
                "temperature": 0.2,
                "response_mime_type": "application/json",
            },
            max_attempts_per_model=3,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Gemini match scoring failed: {exc}",
        ) from exc

    content = getattr(response, "text", None)
    try:
        return _parse_score_payload(content or "")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not parse Gemini match score JSON: {exc}",
        ) from exc


def _upsert_application(
    db: Session,
    *,
    user: User,
    resume: Resume,
    job: Job,
    score: int,
    reasoning: str,
    skill_gaps: list[str],
    tailored_pitch: str,
) -> Application:
    verdict = verdict_from_score(score)
    existing = db.scalar(
        select(Application).where(
            Application.user_id == user.id,
            Application.job_id == job.id,
        )
    )

    # Do not regress applications already past review / apply stages.
    locked = {
        ApplicationStatus.applying,
        ApplicationStatus.applied,
        ApplicationStatus.interviewing,
        ApplicationStatus.offered,
        ApplicationStatus.rejected,
        ApplicationStatus.failed,
        ApplicationStatus.withdrawn,
    }

    if existing is None:
        app = Application(
            user_id=user.id,
            job_id=job.id,
            resume_id=resume.id,
            status=ApplicationStatus.pending_review,
            match_score=score,
            match_verdict=verdict,
            match_reasoning=reasoning,
            skill_gaps=skill_gaps,
            match_pitch=tailored_pitch,
        )
        db.add(app)
        db.flush()
        return app

    if existing.status in locked:
        # Still refresh score metadata without changing status.
        existing.match_score = score
        existing.match_verdict = verdict
        existing.match_reasoning = reasoning
        existing.skill_gaps = skill_gaps
        existing.match_pitch = tailored_pitch
        if existing.resume_id is None:
            existing.resume_id = resume.id
        db.flush()
        return existing

    existing.resume_id = resume.id
    existing.status = ApplicationStatus.pending_review
    existing.match_score = score
    existing.match_verdict = verdict
    existing.match_reasoning = reasoning
    existing.skill_gaps = skill_gaps
    existing.match_pitch = tailored_pitch
    db.flush()
    return existing


def deep_score_matches(
    db: Session,
    user: User,
    *,
    resume_id: UUID | None = None,
    limit: int | None = None,
    min_similarity: float | None = None,
    apply_location_prefs: bool = True,
    persist: bool = True,
    min_match_score: int | None = None,
) -> tuple[Resume, int, list[DeepScoreResult]]:
    resume = resolve_resume_for_match(db, user, resume_id=resume_id)
    top_k = max(1, min(limit or settings.match_deep_score_limit, 20))
    threshold = (
        min_match_score if min_match_score is not None else int(user.min_match_score)
    )
    threshold = max(0, min(100, threshold))

    shortlist: list[MatchCandidate] = search_similar_jobs(
        db,
        resume,
        user,
        limit=top_k,
        min_similarity=min_similarity,
        apply_location_prefs=apply_location_prefs,
    )

    results: list[DeepScoreResult] = []
    for candidate in shortlist:
        try:
            scored = score_job_with_llm(resume, candidate.job)
        except HTTPException as exc:
            logger.warning(
                "deep_score failed job=%s detail=%s",
                candidate.job.id,
                exc.detail,
            )
            continue

        item = DeepScoreResult(
            job=candidate.job,
            similarity=candidate.similarity,
            vector_score=candidate.score,
            score=int(scored["score"]),
            reasoning=str(scored["reasoning"]),
            skill_gaps=list(scored["skill_gaps"]),
            tailored_pitch=str(scored["tailored_pitch"]),
            verdict=verdict_from_score(int(scored["score"])),
        )

        if persist and item.score >= threshold:
            app = _upsert_application(
                db,
                user=user,
                resume=resume,
                job=candidate.job,
                score=item.score,
                reasoning=item.reasoning,
                skill_gaps=item.skill_gaps,
                tailored_pitch=item.tailored_pitch,
            )
            item.persisted = True
            item.application_id = app.id
            item.status = app.status
            item.verdict = app.match_verdict or item.verdict

        results.append(item)

    if persist:
        db.commit()

    results.sort(key=lambda r: r.score, reverse=True)

    persisted_ids = [str(r.application_id) for r in results if r.persisted and r.application_id]
    if persist and persisted_ids:
        try:
            from app.tasks.notify_matches import notify_matches_found

            notify_matches_found.delay(str(user.id), persisted_ids)
        except Exception:  # noqa: BLE001
            logger.exception(
                "failed to queue match notifications user=%s count=%s",
                user.id,
                len(persisted_ids),
            )

    logger.info(
        "deep_score user=%s resume=%s scored=%s persisted=%s threshold=%s",
        user.id,
        resume.id,
        len(results),
        sum(1 for r in results if r.persisted),
        threshold,
    )
    return resume, threshold, results
