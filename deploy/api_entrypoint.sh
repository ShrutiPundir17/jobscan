#!/bin/sh
set -e

# Apply DB migrations before serving (backend) or starting workers.
if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  echo "Running alembic upgrade head..."
  alembic upgrade head
fi

exec "$@"
