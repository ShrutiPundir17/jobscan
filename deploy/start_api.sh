#!/bin/sh
set -e
# SERVICE_ROLE selects process: api (default) | worker | beat
case "${SERVICE_ROLE:-api}" in
  worker)
    exec sh /app/scripts/celery_worker.sh
    ;;
  beat)
    exec celery -A app.celery_app.celery beat --loglevel=info
    ;;
  api|*)
    PORT="${PORT:-8000}"
    exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --proxy-headers --forwarded-allow-ips='*'
    ;;
esac
