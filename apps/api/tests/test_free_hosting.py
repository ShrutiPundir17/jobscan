"""TASK_MODE=inline (Render free, no Celery worker) + GitHub Actions scanning."""

from __future__ import annotations

import threading
import time
from datetime import timedelta

import fakeredis
import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.services import auto_apply as svc
from app.services import scan_guard, scan_status
from app.tasks import dispatch as dispatch_mod
from test_auto_apply import FakeAgent, auth, make_app, make_resume, make_user, reload

from apply_agent import AtsTarget


@pytest.fixture
def r(monkeypatch):
    client = fakeredis.FakeRedis()
    monkeypatch.setattr(scan_guard, "redis_client", lambda: client)
    monkeypatch.setattr(scan_status, "redis_client", lambda: client)
    return client


@pytest.fixture
def inline(monkeypatch):
    monkeypatch.setattr(settings, "task_mode", "inline")


def wait_idle(timeout=10.0):
    """Block until the inline executor has drained."""
    dispatch_mod._pool().submit(lambda: None).result(timeout=timeout)


# --- settings ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("postgres://u:p@aws-0-ap-south-1.pooler.supabase.com:5432/postgres",
         "postgresql+psycopg://u:p@aws-0-ap-south-1.pooler.supabase.com:5432/postgres"),
        ("postgresql://u:p@h:5432/db?sslmode=require", "postgresql+psycopg://u:p@h:5432/db?sslmode=require"),
        ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
    ],
)
def test_database_url_gets_driver(raw, expected):
    assert Settings(database_url=raw, _env_file=None).database_url == expected


def test_task_mode_is_validated():
    assert Settings(task_mode=" INLINE ", _env_file=None).task_mode == "inline"
    with pytest.raises(ValidationError):
        Settings(task_mode="celery", _env_file=None)


# --- dispatch ------------------------------------------------------------------------------


def test_worker_mode_uses_the_queue(monkeypatch):
    calls = []

    class Task:
        name = "t"

        def delay(self, *a, **k):
            calls.append((a, k))
            return "queued"

    assert dispatch_mod.dispatch(Task(), 1, x=2) == "queued"
    assert calls == [((1,), {"x": 2})]


def test_inline_returns_immediately_runs_serially_and_survives_failures(inline):
    running = []
    overlap = []
    lock = threading.Lock()

    def slow(i):
        with lock:
            running.append(i)
            if len(running) > 1:
                overlap.append(i)
        time.sleep(0.2)
        with lock:
            running.remove(i)
        return i

    def boom():
        raise RuntimeError("task failed")

    t0 = time.monotonic()
    futures = [dispatch_mod.dispatch(slow, i) for i in range(3)]
    failing = dispatch_mod.dispatch(boom)
    assert time.monotonic() - t0 < 0.1  # the request is not blocked
    assert [f.result(timeout=5) for f in futures] == [0, 1, 2]
    assert failing.result(timeout=5) is None  # logged, not raised into the pool
    assert overlap == []  # one at a time: Chromium never runs twice on 512 MB


def test_auto_apply_end_to_end_without_a_worker(client, db, inline, monkeypatch):
    agent = FakeAgent()
    monkeypatch.setattr(svc, "_agent", lambda: agent)
    monkeypatch.setattr(svc, "resolve_target", lambda job: AtsTarget("greenhouse", "acme", "1", "https://job-boards.greenhouse.io/acme/jobs/1"))
    user = make_user(db)
    make_resume(db, user)
    app = make_app(db, user)
    h = auth(user)

    r = client.post(f"/applications/{app.id}/auto-apply/prepare", headers=h)
    assert r.status_code == 202
    wait_idle()
    assert client.get(f"/applications/{app.id}", headers=h).json()["auto_apply_state"] == "awaiting_approval"

    assert client.post(f"/applications/{app.id}/auto-apply/approve", headers=h).status_code == 202
    wait_idle()
    final = client.get(f"/applications/{app.id}", headers=h).json()
    assert final["auto_apply_state"] == "submitted" and final["status"] == "applied"
    assert [c[0] for c in agent.calls] == ["prepare", "submit"]


def test_embed_jobs_endpoint_inline(client, db, inline, monkeypatch):
    from app.tasks import job_embeddings

    ran = []
    monkeypatch.setattr(job_embeddings.embed_pending_jobs, "run", lambda batch=None: ran.append(batch) or {"embedded": 0})
    user = make_user(db)
    r = client.post("/scanner/embed-jobs", headers=auth(user), json={"batch_size": 5})
    assert r.status_code == 200 and r.json()["task_id"] == "inline"
    wait_idle()
    assert ran == [5]


# --- scan trigger in inline mode --------------------------------------------------------------


class _Resp:
    def __init__(self, code, text=""):
        self.status_code, self.text = code, text


def test_trigger_without_token_explains_schedule(client, db, r, inline, monkeypatch):
    monkeypatch.setattr(settings, "github_dispatch_token", "")
    monkeypatch.setattr(settings, "scanner_enabled", True)
    user = make_user(db)
    body = client.post("/scanner/trigger", headers=auth(user)).json()
    assert body["status"] == "scheduled"
    assert scan_guard.manual_trigger_wait(user.id, timedelta(minutes=10)) == 0  # no slot consumed


def test_trigger_dispatches_github_workflow(client, db, r, inline, monkeypatch):
    from app.api import scanner as scanner_api

    sent = []
    monkeypatch.setattr(settings, "github_dispatch_token", "ghp_SECRET_TOKEN")
    monkeypatch.setattr(settings, "scanner_enabled", True)
    monkeypatch.setattr(scanner_api.requests, "post", lambda url, **kw: sent.append((url, kw)) or _Resp(204))
    user = make_user(db, target_roles=["Backend Engineer"], preferred_locations=["Pune", "Delhi"])
    r_ = client.post("/scanner/trigger", headers=auth(user))
    assert r_.status_code == 200 and r_.json()["status"] == "queued"
    assert "ghp_SECRET_TOKEN" not in r_.text
    url, kw = sent[0]
    assert url.endswith("/repos/ShrutiPundir17/jobscan/actions/workflows/scan.yml/dispatches")
    assert kw["headers"]["Authorization"] == "Bearer ghp_SECRET_TOKEN"
    assert kw["json"] == {"ref": "main", "inputs": {"keywords": "Backend Engineer", "locations": "Pune,Delhi"}}
    assert client.post("/scanner/trigger", headers=auth(user)).json()["status"] == "already_queued"


def test_trigger_github_failure_releases_slot(client, db, r, inline, monkeypatch):
    from app.api import scanner as scanner_api

    monkeypatch.setattr(settings, "github_dispatch_token", "ghp_SECRET_TOKEN")
    monkeypatch.setattr(settings, "scanner_enabled", True)
    monkeypatch.setattr(scanner_api.requests, "post", lambda url, **kw: _Resp(401, '{"message":"Bad credentials"}'))
    user = make_user(db)
    resp = client.post("/scanner/trigger", headers=auth(user))
    assert resp.status_code == 503 and "ghp_SECRET_TOKEN" not in resp.text
    assert not scan_guard.scan_in_progress()
    assert scan_guard.manual_trigger_wait(user.id, timedelta(minutes=10)) == 0


# --- GitHub Actions entrypoint ------------------------------------------------------------------


def test_preferred_search_terms_are_most_common(db, monkeypatch):
    from app import scan_once

    monkeypatch.setattr(settings, "scanner_max_keywords", 2)
    monkeypatch.setattr(settings, "scanner_max_locations", 1)
    make_user(db, email="a@jobagent.dev", target_roles=["Backend Engineer", "Data Analyst"], preferred_locations=["Pune"])
    make_user(db, email="b@jobagent.dev", target_roles=["backend   engineer"], preferred_locations=["pune", "Delhi"])
    make_user(db, email="c@jobagent.dev", target_roles=["SRE"], preferred_locations=[])
    roles, cities = scan_once.preferred_search_terms()
    assert roles[0] == "Backend Engineer" and len(roles) == 2
    assert cities == ["Pune"]


def test_preferred_search_terms_empty_means_defaults(db):
    from app import scan_once

    make_user(db)
    assert scan_once.preferred_search_terms() == (None, None)


def test_scan_once_embeds_synchronously_and_sets_exit_code(monkeypatch):
    from app import scan_once

    seen = {}

    def fake_run(keywords, locations, *, after_scan):
        seen.update(keywords=keywords, locations=locations)
        after_scan()
        return {"status": seen.get("status", "partial")}

    batches = iter([{"embedded": 40, "pending_remaining": 10}, {"embedded": 10, "pending_remaining": 0}, {"embedded": 99}])
    from app.tasks import job_embeddings

    calls = []
    monkeypatch.setattr(job_embeddings.embed_pending_jobs, "run", lambda batch=None: calls.append(1) or next(batches))
    monkeypatch.setattr(scan_once, "run_scan_job", fake_run)
    assert scan_once.main(["--keywords", "python, go", "--locations", "Pune"]) == 0
    assert seen["keywords"] == ["python", "go"] and seen["locations"] == ["Pune"]
    assert len(calls) == 2  # stopped once nothing was pending

    seen["status"] = "failed"
    batches = iter([{"embedded": 0}])
    assert scan_once.main(["--keywords", "x"]) == 1


def test_run_scan_job_runs_after_scan_inline(r, monkeypatch):
    from app.tasks import job_scanner as js

    async def fake_scan(**kw):
        return {"status": "ok", "scraped": 3}

    monkeypatch.setattr(js, "_run_scan", fake_scan)
    order = []
    result = js.run_scan_job(["k"], ["c"], after_scan=lambda: order.append("embedded"))
    assert result["embed_queued"] is True and order == ["embedded"]
    assert not scan_guard.scan_in_progress()

    def broken():
        raise RuntimeError("gemini down")

    assert js.run_scan_job(after_scan=broken)["embed_queued"] is False
