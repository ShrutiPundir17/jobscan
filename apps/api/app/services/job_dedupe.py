from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlparse, urlunparse


def normalize_job_url(url: str) -> str:
    """Canonicalize a job URL so tracking params don't create duplicates."""
    raw = (url or "").strip()
    if not raw:
        return ""

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "https").lower()
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    # LinkedIn country subdomains point at the same job id
    if host.endswith(".linkedin.com"):
        host = "linkedin.com"

    path = parsed.path or ""
    path = re.sub(r"/{2,}", "/", path).rstrip("/") or "/"

    # Drop query + fragment (utm, tracking, currentJobId duplicates, etc.)
    return urlunparse((scheme, host, path, "", "", ""))


def url_fingerprint(url: str) -> str:
    normalized = normalize_job_url(url)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def extract_external_id(source: str, url: str, external_id: str | None = None) -> str:
    """
    Stable portal id for dedupe.

    Prefer scraper-provided id, else parse from URL, else hash of normalized URL.
    """
    if external_id:
        cleaned = str(external_id).strip()
        if cleaned:
            return cleaned[:255]

    normalized = normalize_job_url(url)
    source_l = (source or "").strip().lower()

    if source_l == "linkedin":
        m = re.search(r"/jobs/view/(?:[^/]*-)?(\d+)$", normalized)
        if m:
            return m.group(1)
        m = re.search(r"(\d{8,})$", normalized)
        if m:
            return m.group(1)

    if source_l == "naukri":
        m = re.search(r"-(\d+)$", normalized)
        if m:
            return m.group(1)

    if source_l == "internshala":
        m = re.search(r"-(\d+)$", normalized)
        if m:
            return m.group(1)

    if source_l == "foundit":
        m = re.search(r"/job/(?:[^/]*-)?(\d+)", normalized)
        if m:
            return m.group(1)

    if source_l == "unstop":
        m = re.search(r"/(\d+)(?:/|$)", normalized)
        if m:
            return m.group(1)

    digest = hashlib.sha256(f"{source_l}:{normalized}".encode("utf-8")).hexdigest()[:32]
    return f"url-{digest}"


def dedupe_key(source: str, url: str, external_id: str | None = None) -> tuple[str, str, str]:
    """Return (source, external_id, url_fingerprint) for a listing."""
    src = (source or "").strip().lower()
    normalized = normalize_job_url(url)
    eid = extract_external_id(src, normalized, external_id)
    return src, eid, url_fingerprint(normalized)


def dedupe_scraped_jobs(jobs: list[Any]) -> list[Any]:
    """Drop duplicate listings inside one scrape batch (keep first)."""
    seen_eid: set[tuple[str, str]] = set()
    seen_fp: set[tuple[str, str]] = set()
    out: list[Any] = []

    for job in jobs:
        source = (getattr(job, "source", None) or "").strip()
        url = (getattr(job, "url", None) or "").strip()
        if not source or not url:
            continue
        src, eid, fp = dedupe_key(source, url, getattr(job, "external_id", None))
        eid_key = (src, eid)
        fp_key = (src, fp)
        if eid_key in seen_eid or fp_key in seen_fp:
            continue
        seen_eid.add(eid_key)
        seen_fp.add(fp_key)

        # Normalize fields on the object when possible
        try:
            job.source = src
            job.external_id = eid
            job.url = normalize_job_url(url)
        except Exception:  # noqa: BLE001
            pass
        out.append(job)

    return out
