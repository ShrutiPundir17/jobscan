from __future__ import annotations

import io
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy import select

from apply_agent import ApplyAgentError, AtsTarget, FormReport, SubmitResult
from app.config import settings
from app.models import Application, ApplicationStatus, Job, Notification, Resume, User
from app.security import create_access_token, hash_password
from app.services import auto_apply as svc

GH_URL = "https://job-boards.greenhouse.io/acme/jobs/123"


# --- fixtures ----------------------------------------------------------------


def make_user(db, email="shruti@jobagent.dev", **kw) -> User:
    user = User(
        email=email,
        hashed_password=hash_password("Secret123!"),
        full_name=kw.pop("full_name", "Shruti Pundir"),
        phone=kw.pop("phone", "+919876543210"),
        **kw,
    )
    db.add(user)
    db.commit()
    return user


def auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(subject=user.id)}"}


def make_job(db, url=GH_URL, source="linkedin", **kw) -> Job:
    job = Job(
        source=source,
        title=kw.pop("title", "Backend Engineer"),
        company="Acme",
        url=url,
        location="Bangalore",
        **kw,
    )
    db.add(job)
    db.commit()
    return job


def make_resume(db, user, **kw) -> Resume:
    resume = Resume(
        user_id=user.id,
        filename=kw.pop("filename", "Shruti Resume.pdf"),
        file_data=kw.pop("file_data", b"%PDF-1.4 resume"),
        raw_text=kw.pop("raw_text", "Shruti Pundir\nPython developer"),
        is_primary=True,
        **kw,
    )
    db.add(resume)
    db.commit()
    return resume


_job_seq = iter(range(1, 10_000))


def make_app(db, user, job=None, **kw) -> Application:
    job = job or make_job(db, url=f"{GH_URL}{next(_job_seq)}")
    app = Application(
        user_id=user.id,
        job_id=job.id,
        status=kw.pop("status", ApplicationStatus.pending_review),
        match_score=85,
        match_verdict="strong",
        match_pitch=kw.pop("match_pitch", "I build Python backends."),
        **kw,
    )
    db.add(app)
    db.commit()
    return app


def ready_report(url=GH_URL) -> FormReport:
    return FormReport(
        provider="greenhouse",
        url=url,
        form_title="Backend Engineer",
        filled=[
            {"key": "first_name", "label": "First Name", "value": "Shruti"},
            {"key": "email", "label": "Email", "value": "shruti@jobagent.dev"},
            {"key": "resume", "label": "Resume", "value": "Shruti_Resume.pdf"},
        ],
        resume_attached=True,
    )


class FakeAgent:
    def __init__(self):
        self.report = ready_report()
        self.submit_result: SubmitResult | None = None
        self.raises: Exception | None = None
        self.calls: list[tuple] = []

    async def prepare(self, url, provider, profile):
        self.calls.append(("prepare", url, provider, profile))
        if self.raises:
            raise self.raises
        return self.report

    async def submit(self, url, provider, profile, *, expected=None):
        self.calls.append(("submit", url, provider, profile, expected))
        if self.raises:
            raise self.raises
        return self.submit_result or SubmitResult(True, self.report, maybe_sent=True, confirmation="thank you for applying")


@pytest.fixture
def fake_agent(monkeypatch):
    agent = FakeAgent()
    monkeypatch.setattr(svc, "_agent", lambda: agent)
    monkeypatch.setattr(svc, "resolve_target", lambda job: AtsTarget("greenhouse", "acme", "123", GH_URL))
    return agent


def reload(db, app) -> Application:
    db.expire_all()
    return db.get(Application, app.id)


def last_attempt(queued, kind):
    return [args for name, args in queued if name == kind][-1]


def prepared(db, user, fake_agent, queued, client, **kw) -> Application:
    """An application taken through prepare -> awaiting_approval for real."""
    app = make_app(db, user, **kw)
    assert client.post(f"/applications/{app.id}/auto-apply/prepare", headers=auth(user)).status_code == 202
    assert svc.run_prepare(*last_attempt(queued, "prepare"))["status"] == "awaiting_approval"
    return reload(db, app)


def approved(db, user, fake_agent, queued, client, **kw) -> Application:
    app = prepared(db, user, fake_agent, queued, client, **kw)
    r = client.post(f"/applications/{app.id}/auto-apply/approve", headers=auth(user))
    assert r.status_code == 202, r.text
    return reload(db, app)


# --- prepare --------------------------------------------------------------------


def test_prepare_queues_task_with_attempt(client, db, queued):
    user = make_user(db)
    app = make_app(db, user)
    r = client.post(f"/applications/{app.id}/auto-apply/prepare", headers=auth(user))
    assert r.status_code == 202, r.text
    assert r.json()["auto_apply_state"] == "queued"
    (name, (app_id, attempt)), = queued
    assert name == "prepare" and app_id == str(app.id)
    assert reload(db, app).auto_apply_attempt == attempt

    assert client.post(f"/applications/{app.id}/auto-apply/prepare", headers=auth(user)).status_code == 409
    assert len(queued) == 1


def test_prepare_requires_owner_and_open_status(client, db, queued):
    owner = make_user(db)
    other = make_user(db, email="other@jobagent.dev")
    app = make_app(db, owner)
    assert client.post(f"/applications/{app.id}/auto-apply/prepare", headers=auth(other)).status_code == 404
    assert client.post(f"/applications/{app.id}/auto-apply/prepare").status_code in (401, 403)
    applied = make_app(db, owner, status=ApplicationStatus.applied)
    assert client.post(f"/applications/{applied.id}/auto-apply/prepare", headers=auth(owner)).status_code == 400
    assert queued == []


def test_platform_kill_switch(client, db, queued, monkeypatch):
    monkeypatch.setattr(settings, "auto_apply_enabled", False)
    user = make_user(db)
    app = make_app(db, user)
    assert client.post(f"/applications/{app.id}/auto-apply/prepare", headers=auth(user)).status_code == 503
    assert reload(db, app).auto_apply_state is None


def _break_broker(monkeypatch, task):
    from app.tasks import auto_apply as tasks

    def boom(*_):
        raise ConnectionError("redis down")

    monkeypatch.setattr(getattr(tasks, task), "delay", boom)


def test_manual_prepare_broker_down_marks_failed(client, db, monkeypatch):
    _break_broker(monkeypatch, "prepare_auto_apply")
    user = make_user(db)
    app = make_app(db, user)
    assert client.post(f"/applications/{app.id}/auto-apply/prepare", headers=auth(user)).status_code == 503
    assert reload(db, app).auto_apply_state == "failed"


def test_auto_prepare_broker_down_leaves_rows_retryable(db, monkeypatch):
    _break_broker(monkeypatch, "prepare_auto_apply")
    user = make_user(db, auto_apply_enabled=True)
    a1, a2 = make_app(db, user), make_app(db, user)
    assert svc.queue_auto_prepare(db, user, [str(a1.id), str(a2.id)]) == 0
    for a in (a1, a2):
        row = reload(db, a)
        assert row.auto_apply_state is None and row.auto_apply_attempt is None


def test_stale_queue_reads_failed_and_old_task_is_ignored(client, db, queued, fake_agent):
    user = make_user(db)
    app = make_app(db, user)
    client.post(f"/applications/{app.id}/auto-apply/prepare", headers=auth(user))
    old_task = last_attempt(queued, "prepare")
    row = reload(db, app)
    row.auto_apply_updated_at = datetime.now(UTC) - timedelta(hours=2)
    db.commit()

    body = client.get(f"/applications/{app.id}", headers=auth(user)).json()
    assert body["auto_apply_state"] == "failed" and "never started" in body["auto_apply_error"]
    assert client.post(f"/applications/{app.id}/auto-apply/prepare", headers=auth(user)).status_code == 202
    assert svc.run_prepare(*old_task) == {"status": "skipped"}  # superseded attempt
    assert fake_agent.calls == []
    assert svc.run_prepare(*last_attempt(queued, "prepare"))["status"] == "awaiting_approval"


# --- worker: prepare ---------------------------------------------------------------


def test_run_prepare_ready_plan_and_snapshot(client, db, queued, fake_agent):
    user = make_user(db, linkedin_url="https://www.linkedin.com/in/shruti")
    make_resume(db, user)
    app = prepared(db, user, fake_agent, queued, client)
    assert app.auto_apply_provider == "greenhouse" and app.auto_apply_url == GH_URL
    plan = app.auto_apply_plan
    assert plan["ready"] and plan["title_match"] is True
    assert plan["resume_source"] == "original_file"
    assert plan["applicant"]["cover_letter"] == "I build Python backends."
    assert app.status == ApplicationStatus.pending_review

    _, url, provider, profile = fake_agent.calls[0]
    assert (profile.first_name, profile.last_name, profile.email) == ("Shruti", "Pundir", "shruti@jobagent.dev")
    assert profile.linkedin_url == "https://www.linkedin.com/in/shruti"
    assert profile.resume_filename == "Shruti_Resume.pdf"

    body = client.get(f"/applications/{app.id}", headers=auth(user)).json()
    assert "applicant" not in body["auto_apply_plan"]  # internal snapshot stays server-side
    note = db.scalar(select(Notification).where(Notification.user_id == user.id))
    assert note.type == "auto_apply_ready"


def test_run_prepare_title_mismatch_is_flagged(client, db, queued, fake_agent):
    fake_agent.report.form_title = "Product Designer"
    user = make_user(db)
    app = prepared(db, user, fake_agent, queued, client)
    assert app.auto_apply_plan["title_match"] is False


def test_run_prepare_needs_manual(client, db, queued, fake_agent):
    fake_agent.report = FormReport(
        provider="greenhouse", url=GH_URL, unanswered_required=["Notice period"], resume_attached=True
    )
    user = make_user(db)
    app = make_app(db, user)
    client.post(f"/applications/{app.id}/auto-apply/prepare", headers=auth(user))
    assert svc.run_prepare(*last_attempt(queued, "prepare"))["status"] == "needs_manual"
    app = reload(db, app)
    assert "Notice period" in app.auto_apply_error


def test_run_prepare_unsupported_portal(client, db, queued, fake_agent, monkeypatch):
    monkeypatch.setattr(svc, "resolve_target", lambda job: None)
    user = make_user(db)
    app = make_app(db, user, job=make_job(db, url="https://www.naukri.com/j-1", source="naukri"))
    client.post(f"/applications/{app.id}/auto-apply/prepare", headers=auth(user))
    assert svc.run_prepare(*last_attempt(queued, "prepare")) == {"status": "unsupported"}
    assert "Apply on naukri" in reload(db, app).auto_apply_error
    assert fake_agent.calls == []


def test_run_prepare_agent_error(client, db, queued, fake_agent):
    fake_agent.raises = ApplyAgentError("Application form returned HTTP 404 (job may be closed).")
    user = make_user(db)
    app = make_app(db, user)
    client.post(f"/applications/{app.id}/auto-apply/prepare", headers=auth(user))
    assert svc.run_prepare(*last_attempt(queued, "prepare"))["status"] == "failed"
    assert "HTTP 404" in reload(db, app).auto_apply_error


def test_discard_during_prepare_wins(client, db, queued, monkeypatch):
    user = make_user(db)
    app = make_app(db, user)
    client.post(f"/applications/{app.id}/auto-apply/prepare", headers=auth(user))
    h = auth(user)

    class DiscardingAgent(FakeAgent):
        async def prepare(self, url, provider, profile):
            assert client.post(f"/applications/{app.id}/auto-apply/cancel", headers=h).status_code == 200
            return self.report

    monkeypatch.setattr(svc, "_agent", lambda: DiscardingAgent())
    monkeypatch.setattr(svc, "resolve_target", lambda job: AtsTarget("greenhouse", "acme", "123", GH_URL))
    assert svc.run_prepare(*last_attempt(queued, "prepare")) == {"status": "superseded"}
    row = reload(db, app)
    assert row.auto_apply_state == "dismissed" and row.auto_apply_plan is None


# --- approve / submit -----------------------------------------------------------------


def test_approve_requires_prepared_plan(client, db, queued):
    user = make_user(db)
    app = make_app(db, user)
    assert client.post(f"/applications/{app.id}/auto-apply/approve", headers=auth(user)).status_code == 409
    assert queued == []


def test_approve_rejects_week_old_plan(client, db, queued, fake_agent):
    user = make_user(db)
    app = prepared(db, user, fake_agent, queued, client)
    app.auto_apply_plan = {**app.auto_apply_plan, "prepared_at": (datetime.now(UTC) - timedelta(days=8)).isoformat()}
    db.commit()
    r = client.post(f"/applications/{app.id}/auto-apply/approve", headers=auth(user))
    assert r.status_code == 409 and "week" in r.json()["detail"]


def test_double_approve_race_only_one_wins(db, queued, fake_agent, client):
    from app.db import SessionLocal

    user = make_user(db)
    app = prepared(db, user, fake_agent, queued, client)
    s1, s2 = SessionLocal(), SessionLocal()
    try:
        a1, a2 = s1.get(Application, app.id), s2.get(Application, app.id)  # both see awaiting_approval
        svc.approve(s1, a1)
        with pytest.raises(Exception) as err:
            svc.approve(s2, a2)
        assert getattr(err.value, "status_code", None) == 409
    finally:
        s1.close()
        s2.close()
    assert [n for n, _ in queued].count("submit") == 1


def test_approve_broker_down_returns_plan_to_user(client, db, queued, fake_agent, monkeypatch):
    user = make_user(db)
    app = prepared(db, user, fake_agent, queued, client)
    _break_broker(monkeypatch, "submit_auto_apply")
    assert client.post(f"/applications/{app.id}/auto-apply/approve", headers=auth(user)).status_code == 503
    assert reload(db, app).auto_apply_state == "awaiting_approval"


def test_submit_uses_approved_snapshot_not_live_data(client, db, queued, fake_agent):
    user = make_user(db)
    make_resume(db, user)
    app = approved(db, user, fake_agent, queued, client)

    # After approval: profile and match pitch change (e.g. Find matches re-scored).
    user.phone = "+910000000000"
    app.match_pitch = "A brand new pitch"
    db.commit()

    assert svc.run_submit(*last_attempt(queued, "submit")) == {"status": "submitted", "error": None}
    _, _, _, profile, expected = fake_agent.calls[-1]
    assert profile.phone == "+919876543210"
    assert profile.cover_letter == "I build Python backends."
    assert expected == {"first_name": "Shruti", "email": "shruti@jobagent.dev", "resume": "Shruti_Resume.pdf"}

    app = reload(db, app)
    assert app.status == ApplicationStatus.applied and app.applied_at is not None
    assert app.auto_apply_state == "submitted"
    h = auth(user)
    assert client.post(f"/applications/{app.id}/auto-apply/prepare", headers=h).status_code == 400
    assert client.post(f"/applications/{app.id}/auto-apply/cancel", headers=h).status_code == 400


def test_duplicate_or_stale_submit_tasks_never_resend(client, db, queued, fake_agent):
    user = make_user(db)
    make_resume(db, user)
    app = approved(db, user, fake_agent, queued, client)
    app_id, attempt = last_attempt(queued, "submit")
    assert svc.run_submit(app_id, "not-the-attempt") == {"status": "skipped"}
    assert svc.run_submit(app_id, attempt)["status"] == "submitted"
    assert svc.run_submit(app_id, attempt) == {"status": "skipped"}  # redelivered task
    assert sum(1 for c in fake_agent.calls if c[0] == "submit") == 1


def test_manual_actions_blocked_while_sending(client, db, queued, fake_agent):
    user = make_user(db)
    app = approved(db, user, fake_agent, queued, client)
    h = auth(user)
    for path in ("auto-apply/cancel", "withdraw", "apply", "auto-apply/prepare"):
        assert client.post(f"/applications/{app.id}/{path}", headers=h).status_code == 409, path


def test_unconfirmed_flow(client, db, queued, fake_agent):
    fake_agent.submit_result = SubmitResult(False, ready_report(), maybe_sent=True, error="no confirmation")
    user = make_user(db)
    make_resume(db, user)
    app = approved(db, user, fake_agent, queued, client)
    assert svc.run_submit(*last_attempt(queued, "submit"))["status"] == "unconfirmed"
    h = auth(user)

    body = client.get(f"/applications/{app.id}", headers=h).json()
    assert body["auto_apply_state"] == "unconfirmed" and body["status"] == "pending_review"
    # Never retried or discarded until the user says what happened.
    assert client.post(f"/applications/{app.id}/auto-apply/prepare", headers=h).status_code == 409
    assert client.post(f"/applications/{app.id}/auto-apply/cancel", headers=h).status_code == 409

    r = client.post(f"/applications/{app.id}/auto-apply/resolve", headers=h, json={"received": False})
    assert r.json()["auto_apply_state"] == "failed"
    assert client.post(f"/applications/{app.id}/auto-apply/resolve", headers=h, json={"received": True}).status_code == 409
    assert client.post(f"/applications/{app.id}/auto-apply/prepare", headers=h).status_code == 202


def test_unconfirmed_resolved_as_received(client, db, queued, fake_agent):
    fake_agent.submit_result = SubmitResult(False, ready_report(), maybe_sent=True, error="no confirmation")
    user = make_user(db)
    make_resume(db, user)
    app = approved(db, user, fake_agent, queued, client)
    svc.run_submit(*last_attempt(queued, "submit"))
    r = client.post(f"/applications/{app.id}/auto-apply/resolve", headers=auth(user), json={"received": True})
    assert r.json()["status"] == "applied" and r.json()["auto_apply_state"] == "submitted"


def test_definitely_not_sent_is_failed_and_retryable(client, db, queued, fake_agent):
    fake_agent.submit_result = SubmitResult(False, ready_report(), maybe_sent=False, error="The form rejected it")
    user = make_user(db)
    make_resume(db, user)
    app = approved(db, user, fake_agent, queued, client)
    assert svc.run_submit(*last_attempt(queued, "submit"))["status"] == "failed"
    app = reload(db, app)
    assert app.status == ApplicationStatus.pending_review and app.applied_at is None
    assert client.post(f"/applications/{app.id}/auto-apply/prepare", headers=auth(user)).status_code == 202


def test_crash_after_browser_started_is_unconfirmed(client, db, queued, fake_agent):
    user = make_user(db)
    make_resume(db, user)
    app = approved(db, user, fake_agent, queued, client)
    fake_agent.raises = RuntimeError("browser died mid-click")
    assert svc.run_submit(*last_attempt(queued, "submit"))["status"] == "unconfirmed"
    assert reload(db, app).auto_apply_state == "unconfirmed"


def test_missing_resume_fails_before_browser(client, db, queued, fake_agent):
    user = make_user(db)
    resume = make_resume(db, user)
    app = approved(db, user, fake_agent, queued, client)
    db.delete(resume)
    db.commit()
    assert svc.run_submit(*last_attempt(queued, "submit"))["status"] == "failed"
    assert not any(c[0] == "submit" for c in fake_agent.calls)


def test_stalled_sending_reads_unconfirmed(client, db, queued, fake_agent):
    user = make_user(db)
    app = approved(db, user, fake_agent, queued, client)
    row = reload(db, app)
    row.auto_apply_state = "sending"
    row.auto_apply_updated_at = datetime.now(UTC) - timedelta(hours=1)
    db.commit()
    body = client.get(f"/applications/{app.id}", headers=auth(user)).json()
    assert body["auto_apply_state"] == "unconfirmed" and "may or may not" in body["auto_apply_error"]
    assert client.post(f"/applications/{app.id}/auto-apply/prepare", headers=auth(user)).status_code == 409


def test_daily_limit_counts_in_flight_and_unconfirmed(client, db, queued, fake_agent, monkeypatch):
    monkeypatch.setattr(settings, "auto_apply_daily_limit", 2)
    user = make_user(db)
    now = datetime.now(UTC)
    make_app(db, user, status=ApplicationStatus.applied, applied_at=now, auto_apply_state="submitted")
    make_app(db, user, auto_apply_state="unconfirmed", auto_apply_updated_at=now)
    app = prepared(db, user, fake_agent, queued, client)
    r = client.post(f"/applications/{app.id}/auto-apply/approve", headers=auth(user))
    assert r.status_code == 429
    assert reload(db, app).auto_apply_state == "awaiting_approval"


def test_daily_limit_rechecked_when_worker_claims(client, db, queued, fake_agent, monkeypatch):
    monkeypatch.setattr(settings, "auto_apply_daily_limit", 1)
    user = make_user(db)
    make_resume(db, user)
    app = approved(db, user, fake_agent, queued, client)
    # Another submission landed after approval.
    make_app(db, user, status=ApplicationStatus.applied, applied_at=datetime.now(UTC), auto_apply_state="submitted")
    assert svc.run_submit(*last_attempt(queued, "submit"))["status"] == "failed"
    assert "limit" in reload(db, app).auto_apply_error
    assert not any(c[0] == "submit" for c in fake_agent.calls)


# --- discard / manual changes / auto-queue ------------------------------------------------


def test_discard_is_remembered_by_auto_queue(client, db, queued, fake_agent):
    user = make_user(db, auto_apply_enabled=True)
    app = prepared(db, user, fake_agent, queued, client)
    r = client.post(f"/applications/{app.id}/auto-apply/cancel", headers=auth(user))
    assert r.json()["auto_apply_state"] == "dismissed" and r.json()["auto_apply_plan"] is None
    queued.clear()
    assert svc.queue_auto_prepare(db, user, [str(app.id)]) == 0
    assert queued == []
    # The user can still start it by hand.
    assert client.post(f"/applications/{app.id}/auto-apply/prepare", headers=auth(user)).status_code == 202


def test_queue_auto_prepare_respects_toggle_and_status(db, queued):
    user = make_user(db)
    a1 = make_app(db, user)
    assert svc.queue_auto_prepare(db, user, [str(a1.id)]) == 0
    user.auto_apply_enabled = True
    db.commit()
    a2 = make_app(db, user, status=ApplicationStatus.applied)
    a3 = make_app(db, user, auto_apply_state="needs_manual")
    assert svc.queue_auto_prepare(db, user, [str(a1.id), str(a2.id), str(a3.id)]) == 1
    assert [args[0] for _, args in queued] == [str(a1.id)]


def test_manual_apply_retires_pending_plan(client, db, queued, fake_agent):
    user = make_user(db)
    app = prepared(db, user, fake_agent, queued, client)
    assert client.post(f"/applications/{app.id}/apply", headers=auth(user)).status_code == 200
    app = reload(db, app)
    assert app.status == ApplicationStatus.applied
    assert app.auto_apply_state == "dismissed" and app.auto_apply_plan is None and app.auto_apply_attempt is None


def test_manual_status_change_allowed_while_unconfirmed(client, db):
    user = make_user(db)
    app = make_app(db, user, auto_apply_state="unconfirmed", auto_apply_updated_at=datetime.now(UTC))
    r = client.patch(f"/applications/{app.id}", headers=auth(user), json={"status": "applied"})
    assert r.status_code == 200 and r.json()["status"] == "applied"
    assert r.json()["auto_apply_state"] == "unconfirmed"


# --- target resolution -----------------------------------------------------------------------


LEVER = "https://jobs.lever.co/acme/0f3c2a1b-1234-4abc-9def-0123456789ab"


class _Resp:
    def __init__(self, text, url="https://in.linkedin.com/jobs/view/1", status_code=200):
        self.text, self.url, self.status_code = text, url, status_code


def test_resolve_target_sources(db, monkeypatch):
    assert svc.resolve_target(make_job(db, url=GH_URL + "4")).provider == "greenhouse"
    one = make_job(db, url="https://x.example/j/1", source="foundit", description=f"Apply: {LEVER}")
    assert svc.resolve_target(one).provider == "lever"
    ambiguous = make_job(
        db, url="https://x.example/j/2", source="foundit",
        description=f"Apply: {LEVER} or see https://boards.greenhouse.io/acme/jobs/9",
    )
    monkeypatch.setattr(svc.requests, "get", lambda *a, **k: pytest.fail("should not fetch non-LinkedIn"))
    assert svc.resolve_target(ambiguous) is None


def test_resolve_target_linkedin_apply_url_only(db, monkeypatch):
    encoded = LEVER.replace(":", "%3A").replace("/", "%2F")
    page = (
        '<a href="https://boards.greenhouse.io/other/jobs/1">similar job</a>'
        f'<code id="applyUrl"><!--"https://www.linkedin.com/jobs/view/externalApply/1?url={encoded}&amp;urlHash=x"--></code>'
    )
    monkeypatch.setattr(svc.requests, "get", lambda *a, **k: _Resp(page))
    li = make_job(db, url="https://in.linkedin.com/jobs/view/backend-4000")
    assert svc.resolve_target(li).company == "acme"

    monkeypatch.setattr(svc.requests, "get", lambda *a, **k: _Resp('<a href="https://boards.greenhouse.io/other/jobs/1">x</a>'))
    assert svc.resolve_target(make_job(db, url="https://in.linkedin.com/jobs/view/easy-1")) is None

    monkeypatch.setattr(svc.requests, "get", lambda *a, **k: _Resp(page, url="https://evil.example/landing"))
    assert svc.resolve_target(make_job(db, url="https://in.linkedin.com/jobs/view/redir-1")) is None


def test_materialize_resume_fallbacks(db, tmp_path):
    user = make_user(db)
    full = make_resume(db, user)
    path, name, source = svc.materialize_resume(full, tmp_path)
    assert source == "original_file" and name == "Shruti_Resume.pdf"
    assert open(path, "rb").read() == b"%PDF-1.4 resume"
    text_only = make_resume(db, user, file_data=None, filename="cv.docx")
    assert svc.materialize_resume(text_only, tmp_path)[1:] == ("cv.txt", "text_fallback")
    assert svc.materialize_resume(None, tmp_path) == (None, None, "missing")


# --- regressions on existing endpoints ----------------------------------------------------------


def test_resume_upload_stores_bytes(client, db, monkeypatch):
    from app.api import resumes as resumes_api

    monkeypatch.setattr(resumes_api, "extract_resume_text", lambda **kw: "Shruti Pundir resume")
    user = make_user(db)
    r = client.post(
        "/resumes/upload",
        headers=auth(user),
        files={"file": ("My CV.pdf", io.BytesIO(b"%PDF-1.4 real"), "application/pdf")},
    )
    assert r.status_code == 201, r.text
    assert "file_data" not in r.json()
    db.expire_all()
    assert db.get(Resume, r.json()["id"]).file_data == b"%PDF-1.4 real"
    assert client.get("/resumes", headers=auth(user)).json()["total"] == 1


def test_preferences_linkedin_url(client, db):
    user = make_user(db)
    h = auth(user)
    r = client.patch("/users/me", headers=h, json={"linkedin_url": "linkedin.com/in/shruti"})
    assert r.json()["linkedin_url"] == "https://linkedin.com/in/shruti"
    assert client.patch("/users/me", headers=h, json={"linkedin_url": "https://evil.com/linkedin.com/x"}).status_code == 422
    body = client.patch("/users/me", headers=h, json={"auto_apply_enabled": True, "min_match_score": 75}).json()
    assert body["auto_apply_enabled"] is True and body["linkedin_url"] == "https://linkedin.com/in/shruti"
    assert client.patch("/users/me", headers=h, json={"linkedin_url": ""}).json()["linkedin_url"] is None


def test_existing_application_endpoints(client, db):
    user = make_user(db)
    h = auth(user)
    app = make_app(db, user)
    listed = client.get("/applications", headers=h).json()
    assert listed["total"] == 1 and listed["items"][0]["auto_apply_state"] is None
    matches = client.get("/matches", headers=h).json()
    assert matches["total"] == 1 and "auto_apply_state" in matches["items"][0]
    assert client.get(f"/matches/{app.id}", headers=h).status_code == 200
    assert client.patch(f"/applications/{app.id}", headers=h, json={"notes": " follow up "}).json()["notes"] == "follow up"
    assert client.patch(f"/applications/{app.id}", headers=h, json={"status": "interviewing"}).json()["status"] == "interviewing"
    assert client.post(f"/applications/{app.id}/withdraw", headers=h).json()["status"] == "withdrawn"
    assert client.post(f"/applications/{app.id}/apply", headers=h).status_code == 400


# --- full round trip: DB -> real browser -> mock ATS ---------------------------------------------

_FORM = b"""<!doctype html><html><body><h1>Backend Engineer</h1>
<p>Thank you for applying to Acme - we read everything.</p>
<form action="/submit" method="post" enctype="multipart/form-data">
<label for="first_name">First Name *</label><input id="first_name" name="first_name" aria-required="true">
<label for="last_name">Last Name *</label><input id="last_name" name="last_name" aria-required="true">
<label for="email">Email *</label><input id="email" name="email" aria-required="true">
<label for="phone">Phone</label><input id="phone" name="phone">
<label for="resume">Resume/CV *</label><input id="resume" name="resume" type="file" aria-required="true">
<button type="submit">Submit application</button></form>
<script>document.getElementById('email').addEventListener('input', () => fetch('/autosave', {method: 'POST', body: 'x'}).catch(() => {}));</script>
</body></html>"""


class _Mock(BaseHTTPRequestHandler):
    posts: list[tuple[str, bytes]] = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = _FORM if self.path == "/jobs/1" else b"<h1>Your application has been submitted!</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        _Mock.posts.append((self.path, self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(303)
        self.send_header("Location", "/done")
        self.send_header("Content-Length", "0")
        self.end_headers()


def test_end_to_end_with_real_browser(client, db, queued, monkeypatch):
    from apply_agent import ApplyAgent

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Mock)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/jobs/1"
    _Mock.posts.clear()
    try:
        monkeypatch.setattr(svc, "resolve_target", lambda job: AtsTarget("greenhouse", "acme", "1", url))
        monkeypatch.setattr(
            svc,
            "_agent",
            lambda: ApplyAgent(allowed_hosts={"127.0.0.1"}, pace=0, timeout_ms=15_000, confirm_timeout_s=10),
        )
        user = make_user(db)
        make_resume(db, user)
        app = make_app(db, user)
        h = auth(user)

        assert client.post(f"/applications/{app.id}/auto-apply/prepare", headers=h).status_code == 202
        assert svc.run_prepare(*last_attempt(queued, "prepare"))["status"] == "awaiting_approval"
        assert _Mock.posts == []  # prepare is a dry run: autosave blocked, no upload

        detail = client.get(f"/applications/{app.id}", headers=h).json()
        assert {f["key"]: f["value"] for f in detail["auto_apply_plan"]["filled"]} == {
            "first_name": "Shruti",
            "last_name": "Pundir",
            "email": "shruti@jobagent.dev",
            "phone": "+919876543210",
            "resume": "Shruti_Resume.pdf",
        }

        assert client.post(f"/applications/{app.id}/auto-apply/approve", headers=h).status_code == 202
        assert svc.run_submit(*last_attempt(queued, "submit"))["status"] == "submitted"
        submits = [b for p, b in _Mock.posts if p == "/submit"]
        assert len(submits) == 1
        for needle in (b"Shruti", b"Pundir", b"shruti@jobagent.dev", b"%PDF-1.4 resume"):
            assert needle in submits[0]

        final = client.get(f"/applications/{app.id}", headers=h).json()
        assert final["status"] == "applied" and final["auto_apply_state"] == "submitted"
    finally:
        httpd.shutdown()


def test_expired_approval_is_never_sent_late(client, db, queued, fake_agent):
    user = make_user(db)
    make_resume(db, user)
    app = approved(db, user, fake_agent, queued, client)
    row = reload(db, app)
    row.auto_apply_updated_at = datetime.now(UTC) - timedelta(hours=2)
    db.commit()
    body = client.get(f"/applications/{app.id}", headers=auth(user)).json()
    assert body["auto_apply_state"] == "failed" and "nothing was sent" in body["auto_apply_error"]
    # The task finally arrives after a long backlog: it must not send.
    assert svc.run_submit(*last_attempt(queued, "submit")) == {"status": "skipped"}
    assert not any(c[0] == "submit" for c in fake_agent.calls)
