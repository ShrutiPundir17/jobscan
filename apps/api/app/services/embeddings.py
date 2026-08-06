from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from google import genai
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Job, Resume


def _require_client() -> genai.Client:
    if not settings.google_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GOOGLE_API_KEY is not configured. Add your Google AI Studio key to .env.",
        )
    return genai.Client(api_key=settings.google_api_key)


def build_resume_embedding_text(resume: Resume) -> str:
    """Flatten resume into text optimized for semantic matching."""
    parts: list[str] = []
    parsed: dict[str, Any] = resume.parsed_data or {}

    if parsed.get("full_name"):
        parts.append(f"Name: {parsed['full_name']}")
    if parsed.get("summary"):
        parts.append(f"Summary: {parsed['summary']}")
    if parsed.get("seniority"):
        parts.append(f"Seniority: {parsed['seniority']}")
    if parsed.get("total_years_experience") is not None:
        parts.append(f"Years of experience: {parsed['total_years_experience']}")
    if parsed.get("skills"):
        parts.append("Skills: " + ", ".join(str(s) for s in parsed["skills"]))

    for exp in parsed.get("experience") or []:
        if not isinstance(exp, dict):
            continue
        title = exp.get("title") or ""
        company = exp.get("company") or ""
        desc = exp.get("description") or ""
        highlights = ", ".join(exp.get("highlights") or [])
        parts.append(f"Experience: {title} at {company}. {desc} {highlights}".strip())

    for project in parsed.get("projects") or []:
        if not isinstance(project, dict):
            continue
        name = project.get("name") or ""
        desc = project.get("description") or ""
        techs = ", ".join(project.get("technologies") or [])
        parts.append(f"Project: {name}. {desc} Technologies: {techs}".strip())

    for edu in parsed.get("education") or []:
        if not isinstance(edu, dict):
            continue
        parts.append(
            "Education: "
            f"{edu.get('degree') or ''} {edu.get('field_of_study') or ''} "
            f"at {edu.get('institution') or ''}".strip()
        )

    for cert in parsed.get("certifications") or []:
        if isinstance(cert, str):
            parts.append(f"Certification: {cert}")
        elif isinstance(cert, dict):
            parts.append(
                "Certification: "
                f"{cert.get('name') or ''} ({cert.get('issuer') or ''})".strip()
            )

    # Fallback / supplement with raw text (truncated)
    if resume.raw_text:
        parts.append(resume.raw_text[:6000])

    text = "\n".join(p for p in parts if p and p.strip())
    if not text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Resume has no text to embed. Upload and parse it first.",
        )
    return text[:20000]


def build_job_embedding_text(job: Job) -> str:
    parts = [
        f"Title: {job.title}",
        f"Company: {job.company}",
    ]
    if job.location:
        parts.append(f"Location: {job.location}")
    if job.employment_type:
        parts.append(f"Employment type: {job.employment_type}")
    raw = job.raw_payload if isinstance(job.raw_payload, dict) else {}
    skills = raw.get("skills") or raw.get("keySkills")
    if isinstance(skills, list) and skills:
        parts.append("Skills: " + ", ".join(str(s) for s in skills[:40]))
    elif isinstance(skills, str) and skills.strip():
        parts.append(f"Skills: {skills.strip()}")
    if job.description:
        parts.append(job.description[:8000])
    return "\n".join(parts)


def generate_embedding(
    text: str,
    *,
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> list[float]:
    client = _require_client()
    try:
        result = client.models.embed_content(
            model=settings.embedding_model,
            contents=text,
            config={
                "task_type": task_type,
                "output_dimensionality": settings.embedding_dimensions,
            },
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Embedding generation failed: {exc}",
        ) from exc

    values = _extract_embedding_values(result)
    if not values:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Embedding API returned an empty vector.",
        )

    if len(values) > settings.embedding_dimensions:
        values = values[: settings.embedding_dimensions]
    if len(values) != settings.embedding_dimensions:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"Embedding size mismatch: got {len(values)}, "
                f"expected {settings.embedding_dimensions}."
            ),
        )
    return values


def _extract_embedding_values(result: Any) -> list[float]:
    # google-genai shapes vary slightly by version
    if hasattr(result, "embeddings") and result.embeddings:
        emb = result.embeddings[0]
        if hasattr(emb, "values"):
            return list(emb.values)
    if hasattr(result, "embedding"):
        emb = result.embedding
        if hasattr(emb, "values"):
            return list(emb.values)
        if isinstance(emb, list):
            return list(emb)
    if isinstance(result, dict):
        if "embeddings" in result and result["embeddings"]:
            first = result["embeddings"][0]
            return list(first.get("values") or first.get("embedding") or [])
        if "embedding" in result:
            return list(result["embedding"])
    return []


def embed_resume(db: Session, resume: Resume, *, commit: bool = True) -> Resume:
    text = build_resume_embedding_text(resume)
    vector = generate_embedding(text, task_type="RETRIEVAL_DOCUMENT")
    resume.embedding = vector
    resume.embedding_model = settings.embedding_model
    resume.embedded_at = datetime.now(UTC)
    db.add(resume)
    if commit:
        db.commit()
        db.refresh(resume)
    else:
        db.flush()
    return resume


def embed_job(db: Session, job: Job, *, commit: bool = True) -> Job:
    text = build_job_embedding_text(job)
    vector = generate_embedding(text, task_type="RETRIEVAL_DOCUMENT")
    job.embedding = vector
    job.embedding_model = settings.embedding_model
    job.embedded_at = datetime.now(UTC)
    db.add(job)
    if commit:
        db.commit()
        db.refresh(job)
    else:
        db.flush()
    return job


def clear_job_embedding(job: Job) -> None:
    """Mark a job for re-embed after content changes."""
    job.embedding = None
    job.embedding_model = None
    job.embedded_at = None
