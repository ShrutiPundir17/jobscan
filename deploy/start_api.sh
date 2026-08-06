#!/bin/sh
set -e
# Railway injects PORT; default 8000 for local Docker.
PORT="${PORT:-8000}"
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --proxy-headers --forwarded-allow-ips='*'
