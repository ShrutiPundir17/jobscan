from __future__ import annotations

import pytest

from app import preflight
from app.config import settings
from conftest import TEST_DATABASE_URL

REF = "postgres.wfbkydeyaiqbjcdhwtgk"
POOLER = f"postgresql://{REF}:{{pw}}@aws-1-ap-southeast-2.pooler.supabase.com:{{port}}/postgres"


@pytest.mark.parametrize(
    "url,expect",
    [
        ("", "not set"),
        (POOLER.format(pw="[YOUR-PASSWORD]", port=5432), "placeholder"),
        ("postgresql://postgres:pw@db.wfbkydeyaiqbjcdhwtgk.supabase.co:5432/postgres", "Direct connection"),
        (POOLER.format(pw="pw", port=6543), "Transaction pooler"),
        ("mysql://u:p@h/db", "must start with postgresql"),
    ],
)
def test_database_url_mistakes_are_explained(url, expect):
    problems = preflight.check_database(url)
    assert len(problems) == 1 and expect in problems[0]


def test_real_database_passes():
    assert preflight.check_database(TEST_DATABASE_URL) == []


def test_unreachable_database_is_explained_without_leaking_password():
    problems = preflight.check_database("postgresql+psycopg://u:SuperSecret9@127.0.0.1:1/x")
    assert len(problems) == 1 and "SuperSecret9" not in problems[0]


@pytest.mark.parametrize(
    "msg,expect",
    [
        ('FATAL: password authentication failed for user "x"', "rejected the password"),
        ("FATAL: Tenant or user not found", "postgres.<project-ref>"),
        ("could not translate host name \"h\" to address", "could not be found"),
        ("connection timeout expired", "paused"),
        ("connection to server failed: Network is unreachable", "Session pooler"),
        ("weird failure postgresql://u:secretpw@h/db", "Could not connect"),
    ],
)
def test_driver_errors_become_hints(msg, expect):
    hint = preflight.explain_db_error(msg, "h")
    assert expect in hint and "secretpw" not in hint


@pytest.mark.parametrize(
    "url,expect",
    [
        ("", "not set"),
        ("redis://localhost:6379/0", "not set"),
        ("https://apn1-xyz.upstash.io", "REST URL"),
        ("ftp://x", "must start with rediss://"),
        ("rediss://default:pw@127.0.0.1:1", "did not answer"),
    ],
)
def test_redis_mistakes_are_warnings(url, expect):
    warnings = preflight.check_redis(url)
    assert len(warnings) == 1 and expect in warnings[0]


def test_main_exit_codes(monkeypatch, capsys):
    monkeypatch.setattr(settings, "database_url", TEST_DATABASE_URL)
    monkeypatch.setattr(settings, "google_api_key", "")
    monkeypatch.setenv("REDIS_URL", "https://rest.upstash.io")
    assert preflight.main() == 0
    out = capsys.readouterr().out
    assert "PREFLIGHT WARNING: REDIS_URL is Upstash's REST URL" in out and "GOOGLE_API_KEY is empty" in out

    monkeypatch.setattr(settings, "database_url", POOLER.format(pw="[YOUR-PASSWORD]", port=5432))
    assert preflight.main() == 1
    assert "PREFLIGHT ERROR" in capsys.readouterr().out


def test_health_ignores_redis_outage_only_in_inline_mode(client, monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0")
    monkeypatch.setattr(settings, "task_mode", "inline")
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["postgres"] == "ok" and r.json()["redis"].startswith("error")
    monkeypatch.setattr(settings, "task_mode", "worker")
    assert client.get("/health").status_code == 503
