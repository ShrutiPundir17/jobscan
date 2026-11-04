from __future__ import annotations

import io
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from apply_agent import ApplyAgentError, AtsTarget, FormReport, SubmitResult
from app.config import settings
from app.models import Application, ApplicationStatus, Job, Resume, User
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
    job = Job(source=source, title="Backend Engineer", company="Acme", url=url, location="Bangalore", **kw)
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


def make_app(db, user, job=None, **kw) -> Application:
    job = job or make_job(db)
    app = Application(
        user_id=user.id,
        job_id=job.id,
        status=kw.pop("status", ApplicationStatus.pending_review),
        match_score=85,
        match_verdict="strong",
        match_pitch="I build Python backends.",
        **kw,
    )
    db.add(app)
    db.commit()
    return app


def ready_report(url=GH_URL) -> FormReport:
    return FormReport(
        provider="greenhouse",
        url=url,
        filled=[
            {"key": "first_name", "label": "First Name", "value": "Shruti"},
            {"key": "email", "label": "Email", "value": "shruti@jobagent.dev"},
            {"key": "resume", "label": "Resume", "value": "Shruti_Resume.pdf"},
        ],
        resume_attached=True,
    )


class FakeAgent:
    def __init__(self, report=None, submit_result=None, raises=None):
        self.report = report or ready_report()
        self.submit_result = submit_result
        self.raises = raises
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
        return self.submit_result or SubmitResult(True, self.report, confirmation="Thank you for applying")


@pytest.fixture
def fake_agent(monkeypatch):
    agent = FakeAgent()
    monkeypatch.setattr(svc, "_agent", lambda: agent)
    return agent


def reload(db, app) -> Application:
    db.expire_all()
    return db.get(Application, app.id)


# --- prepare endpoint ----------------------------------------------------------


def test_prepare_queues_task(client, db, queued):
    user = make_user(db)
    app = make_app(db, user)
    r = client.post(f"/applications/{app.id}/auto-apply/prepare", headers=auth(user))
    assert r.status_code == 202, r.text
    assert r.json()["auto_apply_state"] == "queued"
    assert queued == [("prepare", (str(app.id),))]

    # Second click while the worker owns it -> 409, nothing re-queued.
    r = client.post(f"/applications/{app.id}/auto-apply/prepare", headers=auth(user))
    assert r.status_code == 409
    assert len(queued) == 1


def test_prepare_requires_owner_and_open_status(client, db, queued):
    owner = make_user(db)
    other = make_user(db, email="other@jobagent.dev")
    app = make_app(db, owner)
    assert client.post(f"/applications/{app.id}/auto-apply/prepare", headers=auth(other)).status_code == 404
    assert client.post(f"/applications/{app.id}/auto-apply/prepare").status_code in (401, 403)

    applied = make_app(db, owner, job=make_job(db, url=GH_URL + "9"), status=ApplicationStatus.applied)
    r = client.post(f"/applications/{applied.id}/auto-apply/prepare", headers=auth(owner))
    assert r.status_code == 400
    assert queued == []


def test_platform_kill_switch(client, db, queued, monkeypatch):
    monkeypatch.setattr(settings, "auto_apply_enabled", False)
    user = make_user(db)
    app = make_app(db, user)
    r = client.post(f"/applications/{app.id}/auto-apply/prepare", headers=auth(user))
    assert r.status_code == 503
    assert reload(db, app).auto_apply_state is None


def test_prepare_broker_down_marks_failed(client, db, monkeypatch):
    from app.tasks import auto_apply as tasks

    def boom(*_):
        raise ConnectionError("redis down")

    monkeypatch.setattr(tasks.prepare_auto_apply, "delay", boom)
    user = make_user(db)
    app = make_app(db, user)
    r = client.post(f"/applications/{app.id}/auto-apply/prepare", headers=auth(user))
    assert r.status_code == 503
    assert reload(db, app).auto_apply_state == "failed"


def test_stale_busy_state_can_be_restarted(client, db, queued):
    user = make_user(db)
    app = make_app(
        db,
        user,
        auto_apply_state="preparing",
        auto_apply_updated_at=datetime.now(UTC) - timedelta(hours=1),
    )
    r = client.post(f"/applications/{app.id}/auto-apply/prepare", headers=auth(user))
    assert r.status_code == 202


# --- worker: prepare -------------------------------------------------------------


def test_run_prepare_ready_plan(db, fake_agent):
    user = make_user(db, linkedin_url="https://www.linkedin.com/in/shruti")
    make_resume(db, user)
    app = make_app(db, user, auto_apply_state="queued")

    assert svc.run_prepare(str(app.id)) == {"status": "awaiting_approval"}
    app = reload(db, app)
    assert app.auto_apply_provider == "greenhouse"
    assert app.auto_apply_url == GH_URL
    plan = app.auto_apply_plan
    assert plan["ready"] is True
    assert plan["resume_source"] == "original_file"
    assert plan["cover_letter"] == "I build Python backends."
    assert app.status == ApplicationStatus.pending_review  # nothing submitted

    _, url, provider, profile = fake_agent.calls[0]
    assert (url, provider) == (GH_URL, "greenhouse")
    assert (profile.first_name, profile.last_name) == ("Shruti", "Pundir")
    assert profile.email == "shruti@jobagent.dev"
    assert profile.linkedin_url == "https://www.linkedin.com/in/shruti"
    assert profile.resume_filename == "Shruti_Resume.pdf"


def test_run_prepare_needs_manual(db, fake_agent):
    fake_agent.report = FormReport(
        provider="greenhouse", url=GH_URL, unanswered_required=["Notice period"], resume_attached=True
    )
    user = make_user(db)
    make_resume(db, user)
    app = make_app(db, user, auto_apply_state="queued")
    assert svc.run_prepare(str(app.id))["status"] == "needs_manual"
    app = reload(db, app)
    assert "Notice period" in app.auto_apply_error
    assert app.auto_apply_plan["unanswered_required"] == ["Notice period"]


def test_run_prepare_unsupported_portal(db, fake_agent):
    user = make_user(db)
    job = make_job(db, url="https://www.naukri.com/job-listings-123", source="naukri")
    app = make_app(db, user, job=job, auto_apply_state="queued")
    assert svc.run_prepare(str(app.id)) == {"status": "unsupported"}
    app = reload(db, app)
    assert "Apply on naukri" in app.auto_apply_error
    assert fake_agent.calls == []


def test_run_prepare_agent_error(db, fake_agent):
    fake_agent.raises = ApplyAgentError("Application form returned HTTP 404 (job may be closed).")
    user = make_user(db)
    app = make_app(db, user, auto_apply_state="queued")
    assert svc.run_prepare(str(app.id))["status"] == "failed"
    assert "HTTP 404" in reload(db, app).auto_apply_error


def test_run_prepare_skips_when_cancelled(db, fake_agent):
    user = make_user(db)
    app = make_app(db, user)  # state None: user cancelled before worker picked it up
    assert svc.run_prepare(str(app.id)) == {"status": "skipped"}
    assert fake_agent.calls == []


def test_run_prepare_does_not_overwrite_cancel_during_run(db, monkeypatch):
    from app.db import SessionLocal

    user = make_user(db)
    app = make_app(db, user, auto_apply_state="queued")

    class CancellingAgent(FakeAgent):
        async def prepare(self, url, provider, profile):
            s = SessionLocal()
            row = s.get(Application, app.id)
            row.auto_apply_state = None
            s.commit()
            s.close()
            return self.report

    monkeypatch.setattr(svc, "_agent", lambda: CancellingAgent())
    assert svc.run_prepare(str(app.id)) == {"status": "cancelled"}
    assert reload(db, app).auto_apply_state is None


# --- approve / submit ---------------------------------------------------------------


def _awaiting(db, user, **kw):
    report = ready_report()
    return make_app(
        db,
        user,
        auto_apply_state="awaiting_approval",
        auto_apply_provider="greenhouse",
        auto_apply_url=GH_URL,
        auto_apply_plan=report.to_dict(),
        auto_apply_updated_at=datetime.now(UTC),
        **kw,
    )


def test_approve_requires_prepared_plan(client, db, queued):
    user = make_user(db)
    app = make_app(db, user)
    r = client.post(f"/applications/{app.id}/auto-apply/approve", headers=auth(user))
    assert r.status_code == 409
    assert queued == []


def test_approve_then_submit_marks_applied(client, db, queued, fake_agent):
    user = make_user(db)
    make_resume(db, user)
    app = _awaiting(db, user)
    r = client.post(f"/applications/{app.id}/auto-apply/approve", headers=auth(user))
    assert r.status_code == 202, r.text
    assert r.json()["auto_apply_state"] == "submitting"
    assert queued == [("submit", (str(app.id),))]

    # While submitting, manual changes and cancel are blocked.
    assert client.post(f"/applications/{app.id}/auto-apply/cancel", headers=auth(user)).status_code == 409
    assert client.post(f"/applications/{app.id}/withdraw", headers=auth(user)).status_code == 409
    assert client.post(f"/applications/{app.id}/apply", headers=auth(user)).status_code == 409

    assert svc.run_submit(str(app.id)) == {"status": "submitted"}
    app = reload(db, app)
    assert app.status == ApplicationStatus.applied
    assert app.applied_at is not None
    assert app.auto_apply_plan["confirmation"] == "Thank you for applying"
    # The agent was told exactly what the user approved.
    expected = fake_agent.calls[-1][4]
    assert expected == {"first_name": "Shruti", "email": "shruti@jobagent.dev", "resume": "Shruti_Resume.pdf"}

    # Already submitted: no re-prepare, no cancel.
    assert client.post(f"/applications/{app.id}/auto-apply/prepare", headers=auth(user)).status_code == 400
    assert client.post(f"/applications/{app.id}/auto-apply/cancel", headers=auth(user)).status_code == 400


def test_submit_failure_keeps_status(db, fake_agent):
    fake_agent.submit_result = SubmitResult(False, ready_report(), error="A captcha challenge appeared after submitting.")
    user = make_user(db)
    app = _awaiting(db, user)
    app.auto_apply_state = "submitting"
    db.commit()
    assert svc.run_submit(str(app.id))["status"] == "failed"
    app = reload(db, app)
    assert app.status == ApplicationStatus.pending_review
    assert app.applied_at is None
    assert "captcha" in app.auto_apply_error


def test_submit_skips_unless_submitting(db, fake_agent):
    user = make_user(db)
    app = _awaiting(db, user)
    assert svc.run_submit(str(app.id)) == {"status": "skipped"}
    assert fake_agent.calls == []


def test_submit_refuses_withdrawn(db, fake_agent):
    user = make_user(db)
    app = _awaiting(db, user, status=ApplicationStatus.withdrawn)
    app.auto_apply_state = "submitting"
    db.commit()
    assert svc.run_submit(str(app.id))["status"] == "failed"
    assert fake_agent.calls == []


def test_daily_limit(client, db, queued, monkeypatch):
    monkeypatch.setattr(settings, "auto_apply_daily_limit", 1)
    user = make_user(db)
    make_app(
        db,
        user,
        job=make_job(db, url=GH_URL + "1"),
        status=ApplicationStatus.applied,
        applied_at=datetime.now(UTC) - timedelta(hours=2),
        auto_apply_state="submitted",
    )
    app = _awaiting(db, user)
    r = client.post(f"/applications/{app.id}/auto-apply/approve", headers=auth(user))
    assert r.status_code == 429
    assert reload(db, app).auto_apply_state == "awaiting_approval"
    assert queued == []


def test_cancel_clears_plan(client, db):
    user = make_user(db)
    app = _awaiting(db, user)
    r = client.post(f"/applications/{app.id}/auto-apply/cancel", headers=auth(user))
    assert r.status_code == 200
    body = r.json()
    assert body["auto_apply_state"] is None and body["auto_apply_plan"] is None


def test_manual_apply_drops_pending_plan(client, db):
    user = make_user(db)
    app = _awaiting(db, user)
    r = client.post(f"/applications/{app.id}/apply", headers=auth(user))
    assert r.status_code == 200
    app = reload(db, app)
    assert app.status == ApplicationStatus.applied
    assert app.auto_apply_state is None and app.auto_apply_plan is None


# --- auto-queue after matching ---------------------------------------------------------


def test_queue_auto_prepare_respects_user_toggle(db, queued):
    user = make_user(db)
    a1 = make_app(db, user)
    assert svc.queue_auto_prepare(db, user, [str(a1.id)]) == 0

    user.auto_apply_enabled = True
    db.commit()
    a2 = make_app(db, user, job=make_job(db, url=GH_URL + "2"), status=ApplicationStatus.applied)
    a3 = make_app(db, user, job=make_job(db, url=GH_URL + "3"), auto_apply_state="needs_manual")
    assert svc.queue_auto_prepare(db, user, [str(a1.id), str(a2.id), str(a3.id)]) == 1
    assert queued == [("prepare", (str(a1.id),))]


# --- target resolution ------------------------------------------------------------------


def test_resolve_target_sources(db, monkeypatch):
    lever = "https://jobs.lever.co/acme/0f3c2a1b-1234-4abc-9def-0123456789ab"
    assert svc.resolve_target(make_job(db, url=GH_URL + "4")).provider == "greenhouse"
    assert svc.resolve_target(
        make_job(db, url="https://x.example/job/1", source="foundit", description=f"Apply: {lever}")
    ).provider == "lever"

    class Resp:
        status_code = 200
        text = '<code id="applyUrl"><!--"https://www.linkedin.com/jobs/view/externalApply/1?url=' + (
            lever.replace(":", "%3A").replace("/", "%2F") + '"--></code>'
        )

    fetched: list[str] = []
    monkeypatch.setattr(svc.requests, "get", lambda url, **kw: fetched.append(url) or Resp())
    li = make_job(db, url="https://in.linkedin.com/jobs/view/backend-at-acme-4000")
    assert svc.resolve_target(li).provider == "lever"
    # Non-LinkedIn hosts are never fetched.
    assert svc.resolve_target(make_job(db, url="https://www.naukri.com/j/1", source="naukri")) is None
    assert fetched == ["https://in.linkedin.com/jobs/view/backend-at-acme-4000"]


def test_materialize_resume_fallbacks(db, tmp_path):
    user = make_user(db)
    full = make_resume(db, user)
    path, name, source = svc.materialize_resume(full, tmp_path)
    assert source == "original_file" and name == "Shruti_Resume.pdf"
    assert open(path, "rb").read() == b"%PDF-1.4 resume"

    text_only = make_resume(db, user, file_data=None, filename="cv.docx")
    path, name, source = svc.materialize_resume(text_only, tmp_path)
    assert (name, source) == ("cv.txt", "text_fallback")

    assert svc.materialize_resume(None, tmp_path) == (None, None, "missing")


# --- regressions on existing endpoints ----------------------------------------------------


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
    stored = db.get(Resume, r.json()["id"])
    assert stored.file_data == b"%PDF-1.4 real"
    assert client.get("/resumes", headers=auth(user)).json()["total"] == 1


def test_preferences_linkedin_url(client, db):
    user = make_user(db)
    h = auth(user)
    r = client.patch("/users/me", headers=h, json={"linkedin_url": "linkedin.com/in/shruti"})
    assert r.status_code == 200
    assert r.json()["linkedin_url"] == "https://linkedin.com/in/shruti"
    r = client.patch("/users/me", headers=h, json={"linkedin_url": "https://evil.com/linkedin.com/x"})
    assert r.status_code == 422
    r = client.patch("/users/me", headers=h, json={"auto_apply_enabled": True, "min_match_score": 75})
    body = r.json()
    assert body["auto_apply_enabled"] is True and body["linkedin_url"] == "https://linkedin.com/in/shruti"
    r = client.patch("/users/me", headers=h, json={"linkedin_url": ""})
    assert r.json()["linkedin_url"] is None


def test_existing_application_endpoints(client, db):
    user = make_user(db)
    h = auth(user)
    app = make_app(db, user)

    listed = client.get("/applications", headers=h).json()
    assert listed["total"] == 1 and listed["items"][0]["auto_apply_state"] is None

    matches = client.get("/matches", headers=h).json()
    assert matches["total"] == 1 and "auto_apply_state" in matches["items"][0]
    assert client.get(f"/matches/{app.id}", headers=h).status_code == 200

    r = client.patch(f"/applications/{app.id}", headers=h, json={"notes": " follow up "})
    assert r.json()["notes"] == "follow up"
    r = client.patch(f"/applications/{app.id}", headers=h, json={"status": "interviewing"})
    assert r.json()["status"] == "interviewing"
    r = client.post(f"/applications/{app.id}/withdraw", headers=h)
    assert r.json()["status"] == "withdrawn"
    assert client.post(f"/applications/{app.id}/apply", headers=h).status_code == 400
    assert client.get("/", headers=h).status_code == 200


# --- full round trip: DB -> real browser -> mock ATS ----------------------------------------

_FORM = b"""<!doctype html><html><body><form action="/submit" method="post" enctype="multipart/form-data">
<label for="first_name">First Name *</label><input id="first_name" name="first_name" aria-required="true">
<label for="last_name">Last Name *</label><input id="last_name" name="last_name" aria-required="true">
<label for="email">Email *</label><input id="email" name="email" aria-required="true">
<label for="phone">Phone</label><input id="phone" name="phone">
<label for="resume">Resume/CV *</label><input id="resume" name="resume" type="file" aria-required="true">
<button type="submit">Submit application</button></form></body></html>"""


class _Mock(BaseHTTPRequestHandler):
    posts: list[bytes] = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = _FORM if self.path == "/jobs/1" else b"<h1>Thank you for applying!</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        _Mock.posts.append(self.rfile.read(int(self.headers["Content-Length"])))
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
            svc, "_agent", lambda: ApplyAgent(allowed_hosts={"127.0.0.1"}, pace=0, timeout_ms=15_000, confirm_timeout_s=10)
        )
        user = make_user(db)
        make_resume(db, user)
        app = make_app(db, user)
        h = auth(user)

        assert client.post(f"/applications/{app.id}/auto-apply/prepare", headers=h).status_code == 202
        assert svc.run_prepare(str(app.id))["status"] == "awaiting_approval"
        assert _Mock.posts == []  # prepare never submits

        detail = client.get(f"/applications/{app.id}", headers=h).json()
        values = {f["key"]: f["value"] for f in detail["auto_apply_plan"]["filled"]}
        assert values == {
            "first_name": "Shruti",
            "last_name": "Pundir",
            "email": "shruti@jobagent.dev",
            "phone": "+919876543210",
            "resume": "Shruti_Resume.pdf",
        }

        assert client.post(f"/applications/{app.id}/auto-apply/approve", headers=h).status_code == 202
        assert svc.run_submit(str(app.id)) == {"status": "submitted"}
        assert len(_Mock.posts) == 1
        for needle in (b"Shruti", b"Pundir", b"shruti@jobagent.dev", b"%PDF-1.4 resume"):
            assert needle in _Mock.posts[0]

        final = client.get(f"/applications/{app.id}", headers=h).json()
        assert final["status"] == "applied" and final["auto_apply_state"] == "submitted"
    finally:
        httpd.shutdown()
