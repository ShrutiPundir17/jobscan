"""
Resume tailoring — rewrite experience bullets to align with a job JD.

Uses Gemini. Does not invent employers or fake experience; only reframes
existing bullets/highlights toward the target role's language and priorities.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import HTTPException, status
from google import genai
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.models import Application, Job, Resume, User
from app.services.embeddings import build_job_embedding_text

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert resume writer specializing in ATS-friendly bullet rewriting.

Given a candidate's existing experience/projects and a target job description, rewrite
the resume bullets so they emphasize relevant skills and impact for THAT job.

Hard rules:
- Do NOT invent employers, titles, dates, or achievements that are not supported by the input.
- You MAY rephrase, reorder emphasis, and quantify only when numbers already exist in the input.
- Prefer action verbs and JD keywords naturally (no keyword stuffing).
- Keep each role; rewrite its bullets (3–6 bullets per role when source material allows).
- Also rewrite project bullets when projects are provided.
- Optionally tighten the professional summary toward the JD (still truthful).

Return ONLY valid JSON:
{
  "summary": "<optional rewritten summary or null>",
  "experience": [
    {
      "title": "<same or lightly clarified title>",
      "company": "<same company>",
      "start_date": "<passthrough or null>",
      "end_date": "<passthrough or null>",
      "bullets": ["<rewritten bullet>", "..."]
    }
  ],
  "projects": [
    {
      "name": "<project name>",
      "bullets": ["<rewritten bullet>", "..."],
      "technologies": ["..."]
    }
  ]
}
"""


def _require_gemini_client() -> genai.Client:
    if not settings.google_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GOOGLE_API_KEY is not configured.",
        )
    return genai.Client(
        api_key=settings.google_api_key,
        http_options={"timeout": 90_000},
    )


def _clip(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _experience_payload(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for exp in parsed.get("experience") or []:
        if not isinstance(exp, dict):
            continue
        bullets: list[str] = []
        desc = str(exp.get("description") or "").strip()
        if desc:
            # Split multi-line descriptions into bullet-ish chunks
            for part in re.split(r"[\n•●▪‣]+", desc):
                part = part.strip(" -•\t")
                if part:
                    bullets.append(part)
        for h in exp.get("highlights") or []:
            text = str(h).strip()
            if text:
                bullets.append(text)
        rows.append(
            {
                "title": exp.get("title"),
                "company": exp.get("company"),
                "start_date": exp.get("start_date"),
                "end_date": exp.get("end_date"),
                "bullets": bullets[:12],
            }
        )
    return rows


def _projects_payload(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for project in parsed.get("projects") or []:
        if not isinstance(project, dict):
            continue
        bullets: list[str] = []
        desc = str(project.get("description") or "").strip()
        if desc:
            for part in re.split(r"[\n•●▪‣]+", desc):
                part = part.strip(" -•\t")
                if part:
                    bullets.append(part)
        rows.append(
            {
                "name": project.get("name"),
                "bullets": bullets[:8],
                "technologies": list(project.get("technologies") or [])[:20],
            }
        )
    return rows


def _parse_tailor_json(raw: str) -> dict[str, Any]:
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
        raise ValueError("root must be object")

    experience = payload.get("experience") or []
    projects = payload.get("projects") or []
    if not isinstance(experience, list):
        raise ValueError("experience must be a list")
    if not isinstance(projects, list):
        raise ValueError("projects must be a list")

    cleaned_exp: list[dict[str, Any]] = []
    for row in experience:
        if not isinstance(row, dict):
            continue
        bullets = [str(b).strip() for b in (row.get("bullets") or []) if str(b).strip()]
        if not bullets:
            continue
        cleaned_exp.append(
            {
                "title": (str(row.get("title")).strip() if row.get("title") else None),
                "company": (str(row.get("company")).strip() if row.get("company") else None),
                "start_date": row.get("start_date"),
                "end_date": row.get("end_date"),
                "bullets": bullets[:8],
            }
        )

    cleaned_proj: list[dict[str, Any]] = []
    for row in projects:
        if not isinstance(row, dict):
            continue
        bullets = [str(b).strip() for b in (row.get("bullets") or []) if str(b).strip()]
        techs = [str(t).strip() for t in (row.get("technologies") or []) if str(t).strip()]
        if not bullets and not techs:
            continue
        cleaned_proj.append(
            {
                "name": (str(row.get("name")).strip() if row.get("name") else None),
                "bullets": bullets[:8],
                "technologies": techs[:20],
            }
        )

    summary = payload.get("summary")
    summary_text = str(summary).strip() if summary else None

    if not cleaned_exp and not cleaned_proj and not summary_text:
        raise ValueError("no tailored content returned")

    return {
        "summary": summary_text,
        "experience": cleaned_exp,
        "projects": cleaned_proj,
    }


def render_tailored_resume_text(
    *,
    full_name: str | None,
    tailored: dict[str, Any],
    skills: list[str] | None = None,
) -> str:
    lines: list[str] = []
    if full_name:
        lines.append(full_name)
        lines.append("")
    if tailored.get("summary"):
        lines.append("SUMMARY")
        lines.append(str(tailored["summary"]))
        lines.append("")
    if skills:
        lines.append("SKILLS")
        lines.append(", ".join(str(s) for s in skills[:40]))
        lines.append("")
    if tailored.get("experience"):
        lines.append("EXPERIENCE")
        for exp in tailored["experience"]:
            header = " — ".join(
                p for p in [exp.get("title"), exp.get("company")] if p
            ) or "Role"
            dates = " – ".join(
                str(d) for d in [exp.get("start_date"), exp.get("end_date")] if d
            )
            lines.append(header + (f" ({dates})" if dates else ""))
            for bullet in exp.get("bullets") or []:
                lines.append(f"• {bullet}")
            lines.append("")
    if tailored.get("projects"):
        lines.append("PROJECTS")
        for project in tailored["projects"]:
            name = project.get("name") or "Project"
            lines.append(name)
            techs = project.get("technologies") or []
            if techs:
                lines.append("Tech: " + ", ".join(str(t) for t in techs))
            for bullet in project.get("bullets") or []:
                lines.append(f"• {bullet}")
            lines.append("")
    return "\n".join(lines).strip()


def tailor_resume_for_job(resume: Resume, job: Job) -> dict[str, Any]:
    parsed = resume.parsed_data if isinstance(resume.parsed_data, dict) else {}
    experience = _experience_payload(parsed)
    projects = _projects_payload(parsed)
    if not experience and not projects and not parsed.get("summary") and not resume.raw_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Resume has no parsed experience/projects to tailor. Parse the resume first.",
        )

    candidate = {
        "full_name": parsed.get("full_name"),
        "summary": parsed.get("summary"),
        "skills": parsed.get("skills") or [],
        "experience": experience,
        "projects": projects,
    }
    # Fallback: include a slice of raw text if structured experience is thin
    if not experience and resume.raw_text:
        candidate["raw_resume_excerpt"] = _clip(resume.raw_text, 8000)

    job_text = _clip(build_job_embedding_text(job), 10000)
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"TARGET JOB:\n{job_text}\n\n"
        f"CANDIDATE RESUME (JSON):\n{json.dumps(candidate, ensure_ascii=False)[:14000]}"
    )

    client = _require_gemini_client()
    try:
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config={
                "temperature": 0.3,
                "response_mime_type": "application/json",
            },
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Gemini resume tailoring failed: {exc}",
        ) from exc

    content = getattr(response, "text", None)
    try:
        tailored = _parse_tailor_json(content or "")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not parse tailored resume JSON: {exc}",
        ) from exc

    skills = [str(s) for s in (parsed.get("skills") or []) if str(s).strip()]
    text = render_tailored_resume_text(
        full_name=parsed.get("full_name"),
        tailored=tailored,
        skills=skills,
    )
    return {
        "tailored_bullets": tailored,
        "tailored_resume_text": text,
    }


def tailor_application_resume(
    db: Session,
    user: User,
    application_id,
) -> Application:
    from uuid import UUID

    from sqlalchemy import select

    app_id = application_id if isinstance(application_id, UUID) else UUID(str(application_id))
    app = db.scalar(
        select(Application)
        .options(joinedload(Application.job), joinedload(Application.resume))
        .where(Application.id == app_id, Application.user_id == user.id)
    )
    if app is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")

    resume = app.resume
    if resume is None and app.resume_id is not None:
        resume = db.get(Resume, app.resume_id)
    if resume is None:
        resume = db.scalar(
            select(Resume)
            .where(Resume.user_id == user.id, Resume.is_primary.is_(True))
            .order_by(Resume.updated_at.desc())
        ) or db.scalar(
            select(Resume)
            .where(Resume.user_id == user.id)
            .order_by(Resume.updated_at.desc())
            .limit(1)
        )
    if resume is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No resume available to tailor for this match.",
        )
    if app.job is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Match has no job.")

    result = tailor_resume_for_job(resume, app.job)
    app.resume_id = resume.id
    app.tailored_bullets = result["tailored_bullets"]
    app.tailored_resume_text = result["tailored_resume_text"]
    db.add(app)
    db.commit()
    db.refresh(app)
    _ = app.job
    logger.info(
        "resume_tailored application=%s job=%s exp=%s projects=%s",
        app.id,
        app.job_id,
        len((result["tailored_bullets"] or {}).get("experience") or []),
        len((result["tailored_bullets"] or {}).get("projects") or []),
    )
    return app
