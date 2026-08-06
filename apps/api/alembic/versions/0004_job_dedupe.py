"""Add url_fingerprint for job deduplication."""

from __future__ import annotations

import hashlib
import re
from typing import Sequence, Union
from urllib.parse import urlparse, urlunparse

import sqlalchemy as sa
from alembic import op

revision: str = "0004_job_dedupe"
down_revision: Union[str, None] = "0003_user_preferences"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _normalize_job_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "https").lower()
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.endswith(".linkedin.com"):
        host = "linkedin.com"
    path = re.sub(r"/{2,}", "/", parsed.path or "").rstrip("/") or "/"
    return urlunparse((scheme, host, path, "", "", ""))


def _fingerprint(url: str) -> str:
    return hashlib.sha256(_normalize_job_url(url).encode("utf-8")).hexdigest()


def upgrade() -> None:
    op.add_column("jobs", sa.Column("url_fingerprint", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_jobs_url_fingerprint"), "jobs", ["url_fingerprint"], unique=False)

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, source, url, created_at FROM jobs")).mappings().all()

    # Backfill fingerprints
    for row in rows:
        fp = _fingerprint(row["url"] or "")
        conn.execute(
            sa.text("UPDATE jobs SET url_fingerprint = :fp WHERE id = :id"),
            {"fp": fp or None, "id": row["id"]},
        )

    # Drop older duplicates for same (source, fingerprint)
    op.execute(
        """
        DELETE FROM jobs a
        USING jobs b
        WHERE a.url_fingerprint IS NOT NULL
          AND b.url_fingerprint IS NOT NULL
          AND a.source = b.source
          AND a.url_fingerprint = b.url_fingerprint
          AND a.id <> b.id
          AND a.created_at < b.created_at
        """
    )

    op.create_unique_constraint(
        "uq_jobs_source_url_fingerprint",
        "jobs",
        ["source", "url_fingerprint"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_jobs_source_url_fingerprint", "jobs", type_="unique")
    op.drop_index(op.f("ix_jobs_url_fingerprint"), table_name="jobs")
    op.drop_column("jobs", "url_fingerprint")
