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

# Ensure pgvector exists (Railway default Postgres may need this once).
if [ "${RUN_MIGRATIONS:-1}" = "1" ] && [ -n "${DATABASE_URL:-}" ]; then
  echo "Ensuring pgvector extension..."
  python - <<'PY'
import os, sys
from sqlalchemy import create_engine, text
url = os.environ.get("DATABASE_URL", "")
if not url:
    sys.exit(0)
try:
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    print("pgvector ready")
except Exception as exc:
    print(f"pgvector setup warning: {exc}", file=sys.stderr)
    # Continue — alembic will fail loudly if vector is truly unavailable
PY
fi

# Apply DB migrations before serving (backend) or starting workers.
if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  echo "Running alembic upgrade head..."
  alembic upgrade head
fi

exec "$@"
