"""Shared Redis telemetry for job-scan agent status."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from redis import Redis

from app.config import settings

logger = logging.getLogger(__name__)

LAST_SCAN_AT = "jobagent:last_scan_at"
LAST_SCAN_STATUS = "jobagent:last_scan_status"
LAST_SCAN_SCRAPED = "jobagent:last_scan_scraped"


def _redis_url() -> str:
    # Prefer REDIS_URL; fall back to Celery broker (always set on Railway workers).
    url = (settings.redis_url or "").strip()
    if url and "localhost" not in url:
        return url
    broker = (settings.celery_broker_url or "").strip()
    return broker or url or "redis://localhost:6379/0"


def redis_client() -> Redis:
    return Redis.from_url(_redis_url(), socket_connect_timeout=5, socket_timeout=5)


def record_scan_event(
    *,
    status: str,
    scraped: int | None = None,
    bump_daily: bool = False,
) -> None:
    """Persist last-scan metadata for the dashboard Agent Status panel."""
    try:
        client = redis_client()
        now = datetime.now(UTC)
        now_iso = now.isoformat()
        pipe = client.pipeline()
        pipe.set(LAST_SCAN_AT, now_iso)
        pipe.set(LAST_SCAN_STATUS, status)
        if scraped is not None:
            pipe.set(LAST_SCAN_SCRAPED, str(int(scraped)))
        if bump_daily and scraped:
            day_key = f"jobagent:scanned_day:{now.strftime('%Y-%m-%d')}"
            pipe.incrby(day_key, int(scraped))
            pipe.expire(day_key, 60 * 60 * 48)
        pipe.execute()
        client.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("scan_status_redis_failed err=%s", exc)


def read_scan_telemetry() -> dict[str, str | int | None]:
    """Read scan telemetry keys; values are None when missing."""
    out: dict[str, str | int | None] = {
        "last_scan_at": None,
        "last_scan_status": None,
        "last_scan_scraped": None,
        "scanned_today": None,
    }
    try:
        client = redis_client()
        day_key = f"jobagent:scanned_day:{datetime.now(UTC).strftime('%Y-%m-%d')}"
        raw_at, raw_status, raw_scraped, raw_day = client.mget(
            LAST_SCAN_AT,
            LAST_SCAN_STATUS,
            LAST_SCAN_SCRAPED,
            day_key,
        )
        client.close()

        def _s(v: object) -> str | None:
            if v is None:
                return None
            return v.decode() if isinstance(v, bytes) else str(v)

        out["last_scan_at"] = _s(raw_at)
        out["last_scan_status"] = _s(raw_status)
        scraped_s = _s(raw_scraped)
        out["last_scan_scraped"] = int(scraped_s) if scraped_s and scraped_s.isdigit() else None
        day_s = _s(raw_day)
        out["scanned_today"] = int(day_s) if day_s and day_s.isdigit() else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("scan_status_redis_read_failed err=%s", exc)
    return out
