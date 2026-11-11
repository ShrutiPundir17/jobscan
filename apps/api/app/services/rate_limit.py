"""
Small Redis rate limiter for abuse-prone endpoints (login, password/username recovery).

Fixed window per key: INCR + EXPIRE. Fails open — if Redis is unreachable,
requests are allowed (logging in must not depend on Redis being up).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from app.services.scan_status import redis_client

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Limit:
    scope: str
    limit: int
    window_seconds: int


# Failed logins: per (email, IP) pair so an attacker elsewhere can't lock the owner
# out; per IP to stop spraying; a loose per-email cap slows distributed guessing.
LOGIN_FAIL_PAIR = Limit("login-fail-pair", 10, 15 * 60)
LOGIN_FAIL_IP = Limit("login-fail-ip", 30, 15 * 60)
LOGIN_FAIL_EMAIL = Limit("login-fail-email", 50, 60 * 60)
# Recovery messages cost money (email/WhatsApp) and land in someone's inbox.
FORGOT_TARGET = Limit("forgot-target", 3, 60 * 60)
FORGOT_IP = Limit("forgot-ip", 10, 60 * 60)
RESET_IP = Limit("reset-ip", 20, 60 * 60)
REGISTER_IP = Limit("register-ip", 10, 60 * 60)
GOOGLE_IP = Limit("google-ip", 30, 10 * 60)


def client_ip(request: Request) -> str:
    # uvicorn runs with --proxy-headers, so this is the real client behind Render/Caddy.
    return (request.client.host if request.client else None) or "unknown"


def _key(rule: Limit, identity: str) -> str:
    # Hash identities so emails/phones/IPs aren't stored in Redis in clear text.
    digest = hashlib.sha256(identity.strip().lower().encode()).hexdigest()[:32]
    return f"jobagent:rl:{rule.scope}:{digest}"


def _retry_after(rule: Limit, identity: str) -> int:
    """Seconds until `identity` may try again under `rule` (0 = allowed). Does not count."""
    try:
        r = redis_client()
        key = _key(rule, identity)
        used = int(r.get(key) or 0)
        if used < rule.limit:
            return 0
        return max(int(r.ttl(key) or 0), 1)
    except Exception as exc:  # noqa: BLE001
        logger.warning("rate_limit_unavailable scope=%s err=%s", rule.scope, type(exc).__name__)
        return 0


def record(rule: Limit, identity: str) -> None:
    """Count one event for `identity` under `rule`."""
    try:
        r = redis_client()
        key = _key(rule, identity)
        pipe = r.pipeline()
        # Start the window on first use (SET NX EX works on every Redis/Upstash
        # version, unlike EXPIRE NX); INCR keeps the existing TTL.
        pipe.set(key, 0, ex=rule.window_seconds, nx=True)
        pipe.incr(key)
        pipe.execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("rate_limit_unavailable scope=%s err=%s", rule.scope, type(exc).__name__)


def clear(rule: Limit, identity: str) -> None:
    try:
        redis_client().delete(_key(rule, identity))
    except Exception:  # noqa: BLE001
        pass


def enforce(*checks: tuple[Limit, str], message: str) -> None:
    """Raise 429 if any (rule, identity) is over its limit."""
    wait = max((_retry_after(rule, ident) for rule, ident in checks if ident), default=0)
    if wait:
        minutes = max(1, round(wait / 60))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"{message} Try again in about {minutes} min.",
            headers={"Retry-After": str(wait)},
        )


def hit(*checks: tuple[Limit, str], message: str) -> None:
    """Enforce then count — for endpoints where every attempt counts."""
    enforce(*checks, message=message)
    for rule, ident in checks:
        if ident:
            record(rule, ident)
