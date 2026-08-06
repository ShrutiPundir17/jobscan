#!/bin/sh
set -e

# Install Chromium once into the Playwright cache (mounted or container-local).
if ! python -c "from playwright.sync_api import sync_playwright; sync_playwright().start().chromium.executable_path" >/dev/null 2>&1; then
  echo "Installing Playwright Chromium for job scanner..."
  playwright install chromium
fi

exec celery -A app.celery_app.celery worker --loglevel=info --concurrency=1
