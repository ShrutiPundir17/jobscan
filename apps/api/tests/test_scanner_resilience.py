from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

import fakeredis
import pytest
from celery.exceptions import SoftTimeLimitExceeded

from app.config import settings
from app.services import scan_guard, scan_status
from app.tasks import job_scanner as js
from job_scanner.exceptions import ScraperBlockedError, ScraperNavigationError
from job_scanner.models import ScrapedJob
from test_auto_apply import auth, make_user

NOW = datetime(2026, 11, 4, 12, tzinfo=UTC)


@pytest.fixture
def r(monkeypatch):
    client = fakeredis.FakeRedis()
    monkeypatch.setattr(scan_guard, "redis_client", lambda: client)
    monkeypatch.setattr(scan_status, "redis_client", lambda: client)
    return client


@pytest.fixture
def scan_settings(monkeypatch):
    for k, v in {
        "scanner_enabled": True,
        "scanner_portals": "alpha,beta",
        "scanner_keywords": "python",
        "scanner_locations": "pune",
        "scanner_retries": 2,
        "scanner_retry_base_seconds": 1.0,
        "scanner_max_keywords": 3,
        "scanner_max_locations": 3,
        "scanner_proxy_url": "",
    }.items():
        monkeypatch.setattr(settings, k, v)


def jobs_for(portal, n=2, tag=""):
    return [
        ScrapedJob(source=portal, external_id=f"{portal}{tag}{i}", title=f"Dev {i}", company="Co",
                   url=f"https://{portal}.example/{tag}{i}")
        for i in range(n)
    ]


class Harness:
    """Fake scraper + ingest + sleep that record what the scan loop does."""

    def __init__(self, plan):
        self.plan = plan  # portal -> list of outcomes consumed per call (exception or job list)
        self.calls: list[tuple[str, str, str | None]] = []
        self.sleeps: list[float] = []
        self.ingested: list[tuple[str, int]] = []
        self.proxies: list = []

    async def scrape(self, portal, *, keyword, location, proxy=None, **_):
        self.calls.append((portal, keyword, location))
        self.proxies.append(proxy)
        outcomes = self.plan.get(portal, [])
        outcome = outcomes.pop(0) if outcomes else jobs_for(portal, tag=f"{keyword}{location}")
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def ingest(self, jobs):
        self.ingested.append((jobs[0].source, len(jobs)))
        return {"inserted": len(jobs), "updated": 0, "skipped": 0}

    async def sleep(self, seconds):
        self.sleeps.append(seconds)

    def run(self, **kw):
        return asyncio.run(js._run_scan(scrape=self.scrape, ingest=self.ingest, sleep=self.sleep, **kw))


# --- guard primitives --------------------------------------------------------------------


def test_scan_lock_is_exclusive_and_token_safe(r):
    t1 = scan_guard.acquire_scan_lock(timedelta(minutes=5))
    assert t1 and scan_guard.acquire_scan_lock(timedelta(minutes=5)) is None
    scan_guard.release_scan_lock("someone-else")  # must not free our lock
    assert scan_guard.acquire_scan_lock(timedelta(minutes=5)) is None
    scan_guard.release_scan_lock(t1)
    assert scan_guard.acquire_scan_lock(timedelta(minutes=5)) is not None


def test_guard_fails_open_when_redis_is_down(monkeypatch):
    class Down:
        def __getattr__(self, name):
            raise ConnectionError("redis down")

    monkeypatch.setattr(scan_guard, "redis_client", lambda: Down())
    assert scan_guard.acquire_scan_lock(timedelta(minutes=1))  # scan still runs
    assert scan_guard.portal_cooldown_until("alpha") is None
    assert scan_guard.manual_trigger_wait("u", timedelta(minutes=10)) == 0
    assert scan_guard.record_portal_blocked("alpha", "x") is None
    assert [p["state"] for p in scan_guard.portal_health(["alpha"])] == ["unknown"]


def test_block_cooldown_grows_and_success_resets(r):
    waits = []
    for _ in range(7):
        until = scan_guard.record_portal_blocked("alpha", "403", now=NOW)
        waits.append(until - NOW)
    assert waits[:5] == [timedelta(hours=h) for h in (1, 2, 4, 8, 16)]
    assert waits[5:] == [timedelta(hours=24)] * 2  # capped
    assert scan_guard.portal_cooldown_until("alpha", now=NOW) == NOW + timedelta(hours=24)
    scan_guard.record_portal_success("alpha", 5, now=NOW)
    assert scan_guard.portal_cooldown_until("alpha", now=NOW) is None
    assert scan_guard.record_portal_blocked("alpha", "403", now=NOW) == NOW + timedelta(hours=1)


def test_plain_errors_cool_down_only_after_threshold(r):
    assert scan_guard.record_portal_error("alpha", "timeout", now=NOW) is None
    assert scan_guard.record_portal_error("alpha", "timeout", now=NOW) is None
    assert scan_guard.record_portal_error("alpha", "timeout", now=NOW) == NOW + timedelta(minutes=30)


def test_manual_trigger_throttle(r):
    assert scan_guard.manual_trigger_wait("u1", timedelta(minutes=10)) == 0
    wait = scan_guard.manual_trigger_wait("u1", timedelta(minutes=10))
    assert 0 < wait <= 600
    assert scan_guard.manual_trigger_wait("u2", timedelta(minutes=10)) == 0  # per user
    scan_guard.release_manual_trigger("u1")
    assert scan_guard.manual_trigger_wait("u1", timedelta(minutes=10)) == 0


def test_sanitize_strips_credentials():
    msg = "net::ERR_PROXY at http://bob:s3cret@proxy.example:8080/path " + "x" * 500
    clean = scan_guard.sanitize(msg)
    assert "s3cret" not in clean and "bob" not in clean and "***@proxy.example" in clean
    assert len(clean) == 300


def test_portal_health_states(r):
    scan_guard.record_portal_success("ok", 3, now=NOW)
    scan_guard.record_portal_blocked("blocked", "429", now=datetime.now(UTC))
    scan_guard.record_portal_error("flaky", "timeout")
    states = {p["name"]: p for p in scan_guard.portal_health(["ok", "blocked", "flaky", "new"])}
    assert states["ok"]["state"] == "ok" and states["ok"]["last_scraped"] == 3
    assert states["blocked"]["state"] == "cooling_down" and states["blocked"]["cooldown_until"]
    assert states["blocked"]["last_error_kind"] == "blocked"
    assert states["flaky"]["state"] == "failing"
    assert states["new"]["state"] == "unknown"


# --- scan loop ----------------------------------------------------------------------------


def test_transient_errors_are_retried_with_backoff(r, scan_settings):
    h = Harness({"alpha": [ScraperNavigationError("timeout"), TimeoutError("slow")]})
    summary = h.run()
    assert [c[0] for c in h.calls] == ["alpha", "alpha", "alpha", "beta"]
    assert len(h.sleeps) == 2 and h.sleeps[1] > h.sleeps[0]  # exponential
    assert summary["status"] == "ok" and summary["errors"] == []
    assert h.ingested == [("alpha", 2), ("beta", 2)]  # saved per portal
    assert scan_guard.portal_health(["alpha"])[0]["state"] == "ok"


def test_block_is_not_retried_and_skips_rest_of_portal(r, scan_settings, monkeypatch):
    monkeypatch.setattr(settings, "scanner_keywords", "python,java")
    monkeypatch.setattr(settings, "scanner_locations", "pune,delhi")
    h = Harness({"alpha": [jobs_for("alpha"), ScraperBlockedError("LinkedIn blocked guest search (429)")]})
    summary = h.run()
    alpha_calls = [c for c in h.calls if c[0] == "alpha"]
    assert len(alpha_calls) == 2  # 1 success, 1 block, then the other 2 combos skipped
    assert h.sleeps == []  # no retry on block
    assert summary["by_portal"]["alpha"]["status"] == "blocked"
    assert summary["by_portal"]["beta"]["status"] == "ok"
    assert ("alpha", 2) in h.ingested  # jobs before the block still saved
    assert summary["status"] == "partial"
    assert scan_guard.portal_cooldown_until("alpha") is not None


def test_cooling_down_portal_is_not_touched(r, scan_settings):
    scan_guard.record_portal_blocked("alpha", "403")
    h = Harness({})
    summary = h.run()
    assert {c[0] for c in h.calls} == {"beta"}
    assert summary["by_portal"]["alpha"]["status"] == "cooling_down"
    assert summary["status"] == "partial"


def test_everything_blocked_reports_blocked(r, scan_settings):
    h = Harness({"alpha": [ScraperBlockedError("403")], "beta": [ScraperBlockedError("captcha")]})
    assert h.run()["status"] == "blocked"


def test_exhausted_retries_fail_portal_and_eventually_cool_down(r, scan_settings, monkeypatch):
    monkeypatch.setattr(settings, "scanner_portals", "alpha")
    for run in range(3):
        h = Harness({"alpha": [ScraperNavigationError("timeout")] * 3})
        summary = h.run()
        assert summary["status"] == "failed" and summary["by_portal"]["alpha"]["errors"] == 1
        assert len(h.calls) == 3  # 1 try + 2 retries
    assert scan_guard.portal_cooldown_until("alpha") is not None  # third failed run
    assert Harness({}).run()["by_portal"]["alpha"]["status"] == "cooling_down"


def test_deadline_stops_early_and_keeps_collected_jobs(r, scan_settings):
    h = Harness({})
    ticks = iter([0.0])  # alpha's only combo runs, then time is up before beta
    real = time.monotonic

    def fake_monotonic():
        return next(ticks, 999.0)

    js_time = js.time
    js.time = type("T", (), {"monotonic": staticmethod(fake_monotonic)})
    try:
        summary = h.run(deadline=10.0)
    finally:
        js.time = js_time
    assert real() > 0
    assert summary["truncated"] is True
    assert h.ingested == [("alpha", 2)]
    assert summary["by_portal"]["beta"]["status"] == "not_started"
    assert summary["status"] == "partial"


def test_soft_time_limit_is_not_retried_and_earlier_work_is_saved(r, scan_settings):
    h = Harness({"beta": [SoftTimeLimitExceeded()]})
    with pytest.raises(SoftTimeLimitExceeded):
        h.run()
    assert h.ingested == [("alpha", 2)]
    assert [c[0] for c in h.calls].count("beta") == 1


def test_combinations_are_capped(r, scan_settings, monkeypatch):
    monkeypatch.setattr(settings, "scanner_portals", "alpha")
    monkeypatch.setattr(settings, "scanner_max_keywords", 2)
    monkeypatch.setattr(settings, "scanner_max_locations", 2)
    h = Harness({})
    summary = h.run(keywords=[f"k{i}" for i in range(30)], locations=[f"city{i}" for i in range(30)])
    assert len(h.calls) == 4
    assert summary["keywords"] == ["k0", "k1"] and summary["locations"] == ["city0", "city1"]


def test_ingest_failure_is_reported_not_fatal(r, scan_settings):
    h = Harness({})

    def broken(jobs):
        raise RuntimeError("db down")

    h.ingest = broken
    summary = h.run()
    assert any("saving jobs failed" in e for e in summary["errors"])
    assert summary["status"] == "partial"


def test_proxy_is_parsed_passed_and_never_leaked(r, scan_settings, monkeypatch):
    monkeypatch.setattr(settings, "scanner_proxy_url", "http://bob:s3cret@proxy.example:8080")
    assert js.scanner_proxy() == {"server": "http://proxy.example:8080", "username": "bob", "password": "s3cret"}
    h = Harness({"alpha": [ScraperNavigationError("proxy refused http://bob:s3cret@proxy.example:8080")] * 3})
    summary = h.run()
    assert h.proxies[0]["server"] == "http://proxy.example:8080"
    assert not any("s3cret" in e for e in summary["errors"])
    assert "s3cret" not in (scan_guard.portal_health(["alpha"])[0]["last_error"] or "")


@pytest.mark.parametrize("bad", ["ftp://proxy:21", "not a url", "http://", "http://host:notaport"])
def test_invalid_proxy_fails_closed(r, scan_settings, monkeypatch, bad):
    monkeypatch.setattr(settings, "scanner_proxy_url", bad)
    h = Harness({})
    summary = h.run()
    assert summary["status"] == "failed" and h.calls == []  # never scrape without the configured proxy


# --- task + API -----------------------------------------------------------------------------


def test_overlapping_scan_is_skipped(r, monkeypatch):
    token = scan_guard.acquire_scan_lock(timedelta(minutes=5))
    ran = []

    async def fake_run(**kw):
        ran.append(kw)
        return {"status": "ok", "scraped": 0}

    monkeypatch.setattr(js, "_run_scan", fake_run)
    assert js.scan_jobs.run()["status"] == "skipped" and ran == []
    scan_guard.release_scan_lock(token)

    monkeypatch.setattr("app.tasks.job_embeddings.embed_pending_jobs.delay", lambda *a: None)
    scan_guard.mark_queued()
    assert js.scan_jobs.run()["status"] == "ok" and len(ran) == 1
    assert not scan_guard.scan_in_progress()  # lock released, queued flag cleared


def test_trigger_dedupes_throttles_and_caps(client, db, r, monkeypatch, scan_settings):
    from app.api import scanner as scanner_api

    sent = []
    monkeypatch.setattr(scanner_api.scan_jobs, "delay", lambda **kw: sent.append(kw) or type("R", (), {"id": "t1"})())
    monkeypatch.setattr(settings, "scanner_max_keywords", 2)
    user = make_user(db, target_roles=["A", "B", "C", "D"], preferred_locations=["Pune", "Delhi", "Goa"])
    h = auth(user)

    first = client.post("/scanner/trigger", headers=h)
    assert first.status_code == 200 and first.json()["status"] == "queued"
    assert sent == [{"keywords": ["A", "B"], "locations": ["Pune", "Delhi", "Goa"]}]

    again = client.post("/scanner/trigger", headers=h)  # scan still queued
    assert again.json()["status"] == "already_queued" and len(sent) == 1

    scan_guard.clear_queued()
    throttled = client.post("/scanner/trigger", headers=h)
    assert throttled.status_code == 429 and int(throttled.headers["Retry-After"]) > 0
    assert len(sent) == 1


def test_trigger_broker_down_releases_slot(client, db, r, monkeypatch, scan_settings):
    from app.api import scanner as scanner_api

    def boom(**kw):
        raise ConnectionError("broker down")

    monkeypatch.setattr(scanner_api.scan_jobs, "delay", boom)
    user = make_user(db)
    assert client.post("/scanner/trigger", headers=auth(user)).status_code == 503
    assert not scan_guard.scan_in_progress()
    assert scan_guard.manual_trigger_wait(user.id, timedelta(minutes=10)) == 0  # slot was given back


def test_status_reports_stalled_scan_and_portals(client, db, r, scan_settings):
    user = make_user(db)
    r.set(scan_status.LAST_SCAN_STATUS, "running")
    r.set(scan_status.LAST_SCAN_AT, (datetime.now(UTC) - timedelta(hours=1)).isoformat())
    scan_guard.record_portal_blocked("alpha", "LinkedIn blocked guest search (429)")
    scan_guard.record_portal_success("beta", 7)
    body = client.get("/scanner/status", headers=auth(user)).json()
    assert body["last_scan_status"] == "stalled" and body["state"] == "degraded"
    portals = {p["name"]: p for p in body["portals"]}
    assert portals["alpha"]["state"] == "cooling_down" and portals["beta"]["last_scraped"] == 7

    r.set(scan_status.LAST_SCAN_AT, datetime.now(UTC).isoformat())
    assert client.get("/scanner/status", headers=auth(user)).json()["last_scan_status"] == "running"
