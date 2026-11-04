"""
Find a public ATS application form (Greenhouse / Lever / Ashby) for a job.

Pure string functions — no network. Callers feed in the job URL, scraped
payload, description, or a fetched job page; we return a canonical form URL.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

GREENHOUSE = "greenhouse"
LEVER = "lever"
ASHBY = "ashby"

SUPPORTED_PROVIDERS = (GREENHOUSE, LEVER, ASHBY)

# Hosts we are willing to drive a browser against.
ATS_HOSTS = {
    "boards.greenhouse.io": GREENHOUSE,
    "job-boards.greenhouse.io": GREENHOUSE,
    "job-boards.eu.greenhouse.io": GREENHOUSE,
    "jobs.lever.co": LEVER,
    "jobs.eu.lever.co": LEVER,
    "jobs.ashbyhq.com": ASHBY,
}

# Lookahead so nested URLs (redirect params like ?url=https://...) also match.
_URL_RE = re.compile(r"(?=(https?://[^\s\"'<>\\)]+))", re.IGNORECASE)
_SLUG = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"


@dataclass(frozen=True)
class AtsTarget:
    provider: str
    company: str
    job_id: str
    apply_url: str


def _clean(url: str) -> str:
    return url.strip().rstrip(".,;:!?]}'\"")


def detect_ats(url: str | None) -> AtsTarget | None:
    """Return the canonical application-form target for a single ATS URL."""
    if not url:
        return None
    try:
        parsed = urlparse(_clean(url))
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    host = (parsed.hostname or "").lower()
    provider = ATS_HOSTS.get(host)
    if provider is None:
        return None
    path = parsed.path.rstrip("/")

    if provider == GREENHOUSE:
        if path.endswith("/embed/job_app"):
            qs = parse_qs(parsed.query)
            company = (qs.get("for") or [""])[0]
            job_id = (qs.get("token") or [""])[0]
            if re.fullmatch(_SLUG, company) and job_id.isdigit():
                return _greenhouse(company, job_id, host)
            return None
        m = re.fullmatch(rf"/({_SLUG})/jobs/(\d+)", path)
        if m:
            return _greenhouse(m.group(1), m.group(2), host)
        return None

    if provider == LEVER:
        m = re.fullmatch(rf"/({_SLUG})/({_UUID})(?:/apply)?", path)
        if m:
            company, job_id = m.group(1), m.group(2).lower()
            return AtsTarget(LEVER, company, job_id, f"https://{host}/{company}/{job_id}/apply")
        return None

    m = re.fullmatch(rf"/({_SLUG})/({_UUID})(?:/application)?", path)
    if m:
        company, job_id = m.group(1), m.group(2).lower()
        return AtsTarget(
            ASHBY, company, job_id, f"https://jobs.ashbyhq.com/{company}/{job_id}/application"
        )
    return None


def _greenhouse(company: str, job_id: str, host: str) -> AtsTarget:
    # Legacy boards.greenhouse.io redirects to job-boards.greenhouse.io.
    if host == "boards.greenhouse.io":
        host = "job-boards.greenhouse.io"
    return AtsTarget(GREENHOUSE, company, job_id, f"https://{host}/{company}/jobs/{job_id}")


def find_ats_urls(text: str | None) -> list[AtsTarget]:
    """
    Scan free text / HTML for ATS links, including URL-encoded redirect
    params (e.g. LinkedIn's externalApply?url=https%3A%2F%2Fjobs.lever.co...).
    """
    if not text:
        return []
    variants = [text]
    decoded = html.unescape(text)
    for _ in range(2):
        decoded = unquote(decoded)
        variants.append(decoded)

    found: list[AtsTarget] = []
    seen: set[str] = set()
    for blob in variants:
        for match in _URL_RE.finditer(blob):
            target = detect_ats(match.group(1))
            if target and target.apply_url not in seen:
                seen.add(target.apply_url)
                found.append(target)
    return found


def first_ats_target(*texts: str | None) -> AtsTarget | None:
    for text in texts:
        direct = detect_ats(text)
        if direct:
            return direct
        hits = find_ats_urls(text)
        if hits:
            return hits[0]
    return None
