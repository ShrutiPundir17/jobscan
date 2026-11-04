"""
Scanner resilience state kept in Redis (shared by API and Celery workers).

- One scan at a time: a token lock so the hourly beat and manual triggers
  never overlap (a later scan simply skips).
- Manual triggers: a per-user cooldown plus a "scan already queued" flag, so
  clicking Scan jobs repeatedly can't stack hours of work on the worker.
- Per-portal circuit breaker: a portal that blocks us (403/429/captcha) is
  left alone for an exponentially growing cooldown instead of being hit again
  every hour; repeated plain failures get a short cooldown. Any success resets it.

Every function fails open: if Redis is unavailable the scan still runs (Celery
itself needs the broker, so this is only a degradation of the guard rails).
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from redis import Redis
from redis.exceptions import WatchError

from app.services.scan_status import redis_client

logger = logging.getLogger(__name__)

LOCK_KEY = "jobagent:scan:lock"
QUEUED_KEY = "jobagent:scan:queued"
TRIGGER_KEY = "jobagent:scan:trigger:{user_id}"
PORTAL_KEY = "jobagent:scan:portal:{portal}"

BLOCK_BASE = timedelta(hours=1)
BLOCK_MAX = timedelta(hours=24)
ERROR_THRESHOLD = 3
ERROR_COOLDOWN = timedelta(minutes=30)
QUEUED_TTL = timedelta(minutes=30)
PORTAL_STATE_TTL = timedelta(days=14)

_CREDENTIALS_RE = re.compile(r"(?<=://)[^/@\s]+@")


def sanitize(message: object, limit: int = 300) -> str:
    """Strip URL credentials and cap length before a message is stored or shown."""
    text = _CREDENTIALS_RE.sub("***@", str(message))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _now() -> datetime:
    return datetime.now(UTC)


def _client(client: Redis | None) -> Redis:
    return client if client is not None else redis_client()


def _decode(value: Any) -> str | None:
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else str(value)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# --- single running scan ---------------------------------------------------


def acquire_scan_lock(ttl: timedelta, *, client: Redis | None = None) -> str | None:
    """Return a lock token, or None if another scan holds the lock. Fails open."""
    token = secrets.token_hex(16)
    try:
        ok = _client(client).set(LOCK_KEY, token, nx=True, ex=int(ttl.total_seconds()))
    except Exception as exc:  # noqa: BLE001
        logger.warning("scan_lock_unavailable err=%s — running unguarded", exc)
        return token
    return token if ok else None


def release_scan_lock(token: str, *, client: Redis | None = None) -> None:
    """Delete the lock only if we still own it (an expired lock may belong to a newer scan)."""
    try:
        r = _client(client)
        with r.pipeline() as pipe:
            try:
                pipe.watch(LOCK_KEY)
                if _decode(pipe.get(LOCK_KEY)) == token:
                    pipe.multi()
                    pipe.delete(LOCK_KEY)
                    pipe.execute()
                else:
                    pipe.unwatch()
            except WatchError:
                pass  # changed under us: not ours any more
    except Exception as exc:  # noqa: BLE001
        logger.warning("scan_lock_release_failed err=%s", exc)


def scan_in_progress(*, client: Redis | None = None) -> bool:
    try:
        r = _client(client)
        return bool(r.exists(LOCK_KEY) or r.exists(QUEUED_KEY))
    except Exception:  # noqa: BLE001
        return False


def mark_queued(*, client: Redis | None = None) -> None:
    try:
        _client(client).set(QUEUED_KEY, _now().isoformat(), ex=int(QUEUED_TTL.total_seconds()))
    except Exception as exc:  # noqa: BLE001
        logger.warning("scan_queued_flag_failed err=%s", exc)


def clear_queued(*, client: Redis | None = None) -> None:
    try:
        _client(client).delete(QUEUED_KEY)
    except Exception:  # noqa: BLE001
        pass


def manual_trigger_wait(user_id: object, cooldown: timedelta, *, client: Redis | None = None) -> int:
    """
    Claim the user's manual-scan slot. Returns 0 when allowed, otherwise the
    seconds to wait. Fails open.
    """
    key = TRIGGER_KEY.format(user_id=user_id)
    seconds = int(cooldown.total_seconds())
    if seconds <= 0:
        return 0
    try:
        r = _client(client)
        if r.set(key, "1", nx=True, ex=seconds):
            return 0
        ttl = int(r.ttl(key) or 0)
        return max(ttl, 1)
    except Exception as exc:  # noqa: BLE001
        logger.warning("scan_trigger_throttle_unavailable err=%s", exc)
        return 0


def release_manual_trigger(user_id: object, *, client: Redis | None = None) -> None:
    """Give the slot back when the trigger didn't actually queue anything."""
    try:
        _client(client).delete(TRIGGER_KEY.format(user_id=user_id))
    except Exception:  # noqa: BLE001
        pass


# --- per-portal circuit breaker ---------------------------------------------


def _portal_state(r: Redis, portal: str) -> dict[str, str]:
    raw = r.hgetall(PORTAL_KEY.format(portal=portal)) or {}
    return {_decode(k) or "": _decode(v) or "" for k, v in raw.items()}


def portal_cooldown_until(portal: str, *, client: Redis | None = None, now: datetime | None = None) -> datetime | None:
    try:
        state = _portal_state(_client(client), portal)
    except Exception as exc:  # noqa: BLE001
        logger.warning("portal_state_unavailable portal=%s err=%s", portal, exc)
        return None
    until = _parse_dt(state.get("cooldown_until"))
    return until if until and until > (now or _now()) else None


def _save(r: Redis, portal: str, values: dict[str, Any]) -> None:
    key = PORTAL_KEY.format(portal=portal)
    pipe = r.pipeline()
    pipe.hset(key, mapping={k: ("" if v is None else str(v)) for k, v in values.items()})
    pipe.expire(key, int(PORTAL_STATE_TTL.total_seconds()))
    pipe.execute()


def record_portal_success(portal: str, scraped: int, *, client: Redis | None = None, now: datetime | None = None) -> None:
    try:
        _save(
            _client(client),
            portal,
            {
                "blocks": 0,
                "errors": 0,
                "cooldown_until": "",
                "last_ok_at": (now or _now()).isoformat(),
                "last_scraped": scraped,
                "last_error": "",
                "last_error_kind": "",
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("portal_state_write_failed portal=%s err=%s", portal, exc)


def record_portal_blocked(
    portal: str, detail: object, *, client: Redis | None = None, now: datetime | None = None
) -> datetime | None:
    """Exponential cooldown: 1h, 2h, 4h … capped at 24h. Returns the cooldown end."""
    now = now or _now()
    try:
        r = _client(client)
        blocks = int(_portal_state(r, portal).get("blocks") or 0) + 1
        wait = min(BLOCK_BASE * (2 ** (blocks - 1)), BLOCK_MAX)
        until = now + wait
        _save(
            r,
            portal,
            {
                "blocks": blocks,
                "cooldown_until": until.isoformat(),
                "last_error": sanitize(detail),
                "last_error_kind": "blocked",
                "last_error_at": now.isoformat(),
            },
        )
        return until
    except Exception as exc:  # noqa: BLE001
        logger.warning("portal_state_write_failed portal=%s err=%s", portal, exc)
        return None


def record_portal_error(
    portal: str, detail: object, *, client: Redis | None = None, now: datetime | None = None
) -> datetime | None:
    """Plain failures only cool the portal down after several in a row."""
    now = now or _now()
    try:
        r = _client(client)
        errors = int(_portal_state(r, portal).get("errors") or 0) + 1
        values: dict[str, Any] = {
            "errors": errors,
            "last_error": sanitize(detail),
            "last_error_kind": "error",
            "last_error_at": now.isoformat(),
        }
        until = None
        if errors >= ERROR_THRESHOLD:
            until = now + ERROR_COOLDOWN
            values["cooldown_until"] = until.isoformat()
        _save(r, portal, values)
        return until
    except Exception as exc:  # noqa: BLE001
        logger.warning("portal_state_write_failed portal=%s err=%s", portal, exc)
        return None


def portal_health(portals: list[str], *, client: Redis | None = None, now: datetime | None = None) -> list[dict[str, Any]]:
    """Per-portal state for the status API: ok | cooling_down | failing | unknown."""
    now = now or _now()
    out: list[dict[str, Any]] = []
    try:
        r = _client(client)
        states = {p: _portal_state(r, p) for p in portals}
    except Exception as exc:  # noqa: BLE001
        logger.warning("portal_health_unavailable err=%s", exc)
        states = {p: {} for p in portals}
    for portal in portals:
        s = states.get(portal) or {}
        until = _parse_dt(s.get("cooldown_until"))
        if until and until > now:
            state = "cooling_down"
        elif int(s.get("errors") or 0) > 0 or int(s.get("blocks") or 0) > 0:
            state = "failing"
        elif s.get("last_ok_at"):
            state = "ok"
        else:
            state = "unknown"
        out.append(
            {
                "name": portal,
                "state": state,
                "cooldown_until": until.isoformat() if until and until > now else None,
                "last_ok_at": s.get("last_ok_at") or None,
                "last_scraped": int(s["last_scraped"]) if (s.get("last_scraped") or "").isdigit() else None,
                "last_error": s.get("last_error") or None,
                "last_error_kind": s.get("last_error_kind") or None,
            }
        )
    return out
