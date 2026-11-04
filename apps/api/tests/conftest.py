"""
API tests run against a real Postgres + pgvector (JSONB, native enums, vectors).

Point TEST_DATABASE_URL at a throwaway database, e.g.:

    docker run -d --name jobagent-test-pg -p 55499:5432 \
      -e POSTGRES_USER=jobagent -e POSTGRES_PASSWORD=jobagent -e POSTGRES_DB=jobagent_test \
      pgvector/pgvector:pg16

Every test truncates all tables — never point this at real data.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(REPO_ROOT / "workers" / "job-scanner"))
sys.path.insert(0, str(REPO_ROOT / "workers" / "apply-agent"))

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://jobagent:jobagent@localhost:55499/jobagent_test",
)
if "test" not in TEST_DATABASE_URL.rsplit("/", 1)[-1]:
    raise RuntimeError("TEST_DATABASE_URL must name a database containing 'test'.")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["AUTO_APPLY_ENABLED"] = "true"
os.environ["UPLOAD_DIR"] = str(Path(os.environ.get("TMP", "/tmp")) / "jobagent-test-uploads")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    engine.dispose()

    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    command.upgrade(cfg, "head")
    # Round-trip the newest migration to prove downgrade works.
    command.downgrade(cfg, "-1")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def clean_tables(migrated_db):
    from app.db import engine

    yield
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE notifications, applications, password_reset_tokens, "
                "resumes, jobs, users RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture
def db():
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


@pytest.fixture
def queued(monkeypatch):
    """Capture Celery .delay() calls instead of hitting a broker."""
    from app.tasks import auto_apply as tasks
    from app.tasks import notify_matches

    calls: list[tuple[str, tuple]] = []

    def recorder(name):
        return lambda *args: calls.append((name, args))

    monkeypatch.setattr(tasks.prepare_auto_apply, "delay", recorder("prepare"))
    monkeypatch.setattr(tasks.submit_auto_apply, "delay", recorder("submit"))
    monkeypatch.setattr(notify_matches.notify_matches_found, "delay", recorder("notify"))
    return calls
