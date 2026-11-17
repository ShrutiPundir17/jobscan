"""
Startup self-check: explain misconfiguration in plain words in the deploy log.

Runs from deploy/api_entrypoint.sh before migrations. Exits 1 only for problems
that would make the deploy fail anyway (no usable database), so the log ends
with the reason instead of a stack trace. Never prints passwords or keys.

    python -m app.preflight
"""

from __future__ import annotations

import os
import re
import sys
from urllib.parse import urlparse

from sqlalchemy import create_engine, text

PLACEHOLDERS = ("[YOUR-PASSWORD]", "YOUR-PASSWORD", "[YOUR_PASSWORD]", "<password>")


def mask_url(url: str) -> str:
    """postgresql://user:secret@host/db -> postgresql://user:***@host/db"""
    return re.sub(r"(://[^:/@]+):[^@]*@", r"\1:***@", url or "")


def check_database(url: str) -> list[str]:
    """Return fatal problems with DATABASE_URL (empty list = OK)."""
    if not url:
        return ["DATABASE_URL is not set. Paste your Supabase 'Session pooler' URI into the service settings."]
    if any(p in url for p in PLACEHOLDERS):
        return ["DATABASE_URL still contains the [YOUR-PASSWORD] placeholder — replace it with your database password."]
    parsed = urlparse(url)
    if not parsed.scheme.startswith("postgres"):
        return [f"DATABASE_URL must start with postgresql:// (got '{parsed.scheme}://')."]
    host = parsed.hostname or ""
    if re.fullmatch(r"db\.[a-z0-9]+\.supabase\.co", host):
        return [
            "DATABASE_URL is Supabase's 'Direct connection' (IPv6-only on the free plan, unreachable from "
            "Render). In Supabase: Connect -> Direct -> Connection method 'Session pooler' -> copy that URI."
        ]
    if "pooler.supabase.com" in host and parsed.port == 6543:
        return ["DATABASE_URL uses Supabase's Transaction pooler (port 6543). Use the 'Session pooler' URI (port 5432)."]
    try:
        engine = create_engine(url, pool_pre_ping=False, connect_args={"connect_timeout": 10})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
    except Exception as exc:  # noqa: BLE001
        return [explain_db_error(str(exc), host)]
    return []


def explain_db_error(message: str, host: str) -> str:
    m = message.lower()
    if "password authentication failed" in m:
        return "Database rejected the password in DATABASE_URL. Reset it in Supabase (letters and numbers only) and update the URI."
    if "tenant or user not found" in m:
        return "Supabase pooler doesn't know this user. Copy the Session pooler URI again — the user must look like postgres.<project-ref>."
    if "could not translate host name" in m or "name or service not known" in m or "nodename nor servname" in m:
        return f"Database host '{host}' could not be found. Check DATABASE_URL for typos."
    if "timeout" in m or "timed out" in m:
        return f"Timed out connecting to '{host}'. Is the Supabase project paused? Restore it in the Supabase dashboard."
    if "network is unreachable" in m:
        return "Network unreachable (usually the IPv6-only Direct connection). Use the Session pooler URI."
    # Unknown: show the driver's first line with any credentials masked.
    first = mask_url(message.strip().splitlines()[0] if message.strip() else "unknown error")
    return f"Could not connect to the database: {first[:300]}"


def check_redis(url: str) -> list[str]:
    """Non-fatal Redis warnings."""
    if not url or "localhost" in url:
        return ["REDIS_URL is not set — scan status and cooldowns won't be shared. Paste your Upstash rediss:// URL."]
    if url.startswith(("http://", "https://")):
        return ["REDIS_URL is Upstash's REST URL. Use the Redis URL that starts with rediss:// instead."]
    if not url.startswith(("redis://", "rediss://")):
        return ["REDIS_URL must start with rediss:// (Upstash) or redis://."]
    try:
        from redis import Redis

        client = Redis.from_url(url, socket_connect_timeout=5, socket_timeout=5)
        client.ping()
        client.close()
    except Exception as exc:  # noqa: BLE001
        return [f"Redis did not answer ({type(exc).__name__}). Check REDIS_URL (Upstash: the rediss:// URL with its password)."]
    return []


def main() -> int:
    from app.config import settings

    print("Preflight: checking configuration…", flush=True)
    fatal = check_database(settings.database_url)
    warnings = check_redis(os.environ.get("REDIS_URL", settings.redis_url))
    if not settings.google_api_key:
        warnings.append("GOOGLE_API_KEY is empty — resume parsing and matching will fail until it is set.")
    if settings.app_env == "production" and settings.jwt_secret_key.startswith("change-me"):
        warnings.append("JWT_SECRET_KEY is the development default — set a long random value.")

    for w in warnings:
        print(f"PREFLIGHT WARNING: {w}", flush=True)
    for f in fatal:
        print(f"PREFLIGHT ERROR: {f}", flush=True)
    if fatal:
        print("Preflight failed — fix the setting above and redeploy.", flush=True)
        return 1
    print("Preflight: database OK" + (" (with warnings above)" if warnings else ""), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
