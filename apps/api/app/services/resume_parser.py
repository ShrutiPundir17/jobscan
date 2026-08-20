from __future__ import annotations

import json
import re
from calendar import month_abbr, month_name
from datetime import date
from typing import Any, Literal

from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from app.services.gemini_client import generate_content_with_retries

SeniorityLevel = Literal["intern", "junior", "mid", "senior", "lead", "executive", "unknown"]


class ExperienceItem(BaseModel):
    title: str | None = None
    company: str | None = None
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool = False
    description: str | None = None
    highlights: list[str] = Field(default_factory=list)


class EducationItem(BaseModel):
    institution: str | None = None
    degree: str | None = None
    field_of_study: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class ProjectItem(BaseModel):
    name: str | None = None
    description: str | None = None
    technologies: list[str] = Field(default_factory=list)
    url: str | None = None


class CertificationItem(BaseModel):
    name: str | None = None
    issuer: str | None = None
    date_issued: str | None = None


class ParsedResumeData(BaseModel):
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    summary: str | None = None
    seniority: SeniorityLevel = "unknown"
    total_years_experience: float | None = None
    skills: list[str] = Field(default_factory=list)
    experience: list[ExperienceItem] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    certifications: list[CertificationItem] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)


SYSTEM_PROMPT = """You are an expert technical recruiter and resume parser.
Extract structured candidate data from the resume text.
Be accurate. Do not invent employers, degrees, or skills that are not supported by the text.
If something is missing, use null or an empty list.
Infer seniority from titles, years, and scope (intern/junior/mid/senior/lead/executive/unknown).
Normalize skill names (e.g. "JS" -> "JavaScript") when obvious.

For each experience entry:
- Always include start_date and end_date when present on the resume.
- Prefer YYYY-MM format (example: 2025-07).
- If the role says Present/Current/Now, set end_date to null and is_current to true.
- Include internships and part-time roles in experience.
- Do NOT drop current roles even if the start year looks recent.

total_years_experience is optional; it will be recalculated from dates.

Return a single JSON object with exactly these keys:
full_name, email, phone, location, linkedin_url, summary, seniority,
total_years_experience, skills, experience, education, projects, certifications, languages.

certifications must be an array of objects: {"name": string|null, "issuer": string|null, "date_issued": string|null}.
experience items must include: title, company, location, start_date, end_date, is_current, description, highlights.
"""


_MONTHS = {
    **{name.lower(): idx for idx, name in enumerate(month_name) if name},
    **{name.lower(): idx for idx, name in enumerate(month_abbr) if name},
}


def _parse_month_date(value: str | None, *, default_day: int = 1) -> date | None:
    if not value:
        return None
    text = value.strip().lower()
    if text in {"present", "current", "now", "ongoing", "till date", "to date"}:
        return date.today()

    # YYYY-MM or YYYY-MM-DD
    m = re.match(r"^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?$", text)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        day = int(m.group(3) or default_day)
        return date(year, month, min(day, 28))

    # Month YYYY / Mon YYYY
    m = re.match(r"^([a-z]+)\s+(\d{4})$", text)
    if m and m.group(1) in _MONTHS:
        return date(int(m.group(2)), _MONTHS[m.group(1)], 1)

    # YYYY only
    m = re.match(r"^(\d{4})$", text)
    if m:
        return date(int(m.group(1)), 1 if default_day == 1 else 12, 1)

    return None


def _months_between(start: date, end: date) -> int:
    if end < start:
        return 0
    return (end.year - start.year) * 12 + (end.month - start.month) + 1


def recalculate_years_experience(experience: list[ExperienceItem]) -> float | None:
    """Sum role durations from dates so current/Present roles are counted."""
    today = date.today()
    intervals: list[tuple[date, date]] = []

    for item in experience:
        start = _parse_month_date(item.start_date, default_day=1)
        if start is None:
            continue
        if item.is_current or not item.end_date:
            end = today
        else:
            end = _parse_month_date(item.end_date, default_day=28) or today
        if end < start:
            continue
        intervals.append((start, end))

    if not intervals:
        return None

    # Merge overlapping intervals so concurrent roles aren't double-counted.
    intervals.sort(key=lambda pair: pair[0])
    merged: list[list[date]] = [[intervals[0][0], intervals[0][1]]]
    for start, end in intervals[1:]:
        last = merged[-1]
        if start <= last[1]:
            if end > last[1]:
                last[1] = end
        else:
            merged.append([start, end])

    total_months = sum(_months_between(start, end) for start, end in merged)
    return round(total_months / 12, 1)


def _normalize_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Coerce common LLM shape differences before Pydantic validation."""
    certs = data.get("certifications")
    if isinstance(certs, list):
        normalized: list[dict[str, Any]] = []
        for item in certs:
            if isinstance(item, str):
                normalized.append({"name": item, "issuer": None, "date_issued": None})
            elif isinstance(item, dict):
                normalized.append(
                    {
                        "name": item.get("name") or item.get("title"),
                        "issuer": item.get("issuer") or item.get("organization"),
                        "date_issued": item.get("date_issued")
                        or item.get("date")
                        or item.get("year"),
                    }
                )
        data["certifications"] = normalized

    seniority = data.get("seniority")
    if isinstance(seniority, str):
        data["seniority"] = seniority.strip().lower()

    experience = data.get("experience")
    if isinstance(experience, list):
        for item in experience:
            if not isinstance(item, dict):
                continue
            end = str(item.get("end_date") or "").strip().lower()
            if end in {"present", "current", "now", "ongoing", "till date", "to date"}:
                item["end_date"] = None
                item["is_current"] = True
            elif item.get("is_current") is True:
                item["end_date"] = None

    return data


def parse_resume_text(raw_text: str) -> dict[str, Any]:
    text = (raw_text or "").strip()
    if not text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Resume has no extracted text to parse.",
        )

    today = date.today().isoformat()
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Today's date is {today}. Treat Present/Current roles as ongoing through today.\n\n"
        "Parse this resume into structured JSON.\n\n"
        f"RESUME TEXT:\n{text[:30000]}"
    )

    try:
        response = generate_content_with_retries(
            contents=prompt,
            config={
                "temperature": 0,
                "response_mime_type": "application/json",
            },
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Gemini parsing failed: {exc}",
        ) from exc

    content = getattr(response, "text", None)
    if not content:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gemini returned an empty parse response.",
        )

    try:
        payload = _normalize_payload(json.loads(content))
        parsed = ParsedResumeData.model_validate(payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not validate parsed resume JSON: {exc}",
        ) from exc

    calculated = recalculate_years_experience(parsed.experience)
    if calculated is not None:
        parsed.total_years_experience = calculated

    return parsed.model_dump()
