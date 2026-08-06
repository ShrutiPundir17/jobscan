#!/bin/sh
set -e

# Railway/Heroku-style URLs are postgresql:// — SQLAlchemy needs the psycopg driver.
if [ -n "${DATABASE_URL:-}" ]; then
  case "$DATABASE_URL" in
    postgresql+psycopg://*|postgres+psycopg://*) ;;
    postgresql://*)
      export DATABASE_URL="postgresql+psycopg://${DATABASE_URL#postgresql://}"
      ;;
    postgres://*)
      export DATABASE_URL="postgresql+psycopg://${DATABASE_URL#postgres://}"
      ;;
  esac
fi

# Apply DB migrations before serving (backend) or starting workers.
if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  echo "Running alembic upgrade head..."
  alembic upgrade head
fi

exec "$@"
