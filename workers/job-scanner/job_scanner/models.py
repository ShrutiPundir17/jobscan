from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ScrapedJob:
    """Normalized job payload produced by a portal scraper."""

    source: str
    external_id: str | None
    title: str
    company: str
    url: str
    location: str | None = None
    description: str | None = None
    employment_type: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    currency: str | None = None
    posted_at: datetime | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.posted_at is not None:
            data["posted_at"] = self.posted_at.isoformat()
        return data
