from __future__ import annotations

import fakeredis
import pytest
from sqlalchemy import select

from app.config import settings
from app.models import User
from app.services import google_auth
from app.services import rate_limit as rl
from test_auto_apply import auth, make_app, make_user

CLIENT_ID = "531797416174-ours.apps.googleusercontent.com"


@pytest.fixture(autouse=True)
def r(monkeypatch):
    client = fakeredis.FakeRedis()
    monkeypatch.setattr(rl, "redis_client", lambda: client)
    return client


class _Resp:
    def __init__(self, status, payload):
        self.status_code, self._payload, self.text = status, payload, str(payload)

    def json(self):
        return self._payload


# --- Google sign-in: token substitution ----------------------------------------------------------


@pytest.fixture
def google(monkeypatch):
    monkeypatch.setattr(settings, "google_oauth_client_id", CLIENT_ID)
    state = {
        "tokeninfo": {"aud": CLIENT_ID, "azp": CLIENT_ID, "sub": "g-1", "expires_in": "3500"},
        "userinfo": {"sub": "g-1", "email": "victim@gmail.com", "email_verified": True, "name": "Victim"},
    }

    def fake_get(url, **kw):
        if "tokeninfo" in url:
            return _Resp(200, state["tokeninfo"])
        return _Resp(200, state["userinfo"])

    monkeypatch.setattr(google_auth.requests, "get", fake_get)
    return state


def test_google_token_issued_to_another_app_is_refused(client, db, google):
    make_user(db, email="victim@gmail.com")
    google["tokeninfo"]["aud"] = google["tokeninfo"]["azp"] = "999-evil-site.apps.googleusercontent.com"
    r = client.post("/auth/google", json={"access_token": "token-from-another-site"})
    assert r.status_code == 401 and "not issued for JobAgent" in r.json()["detail"]


def test_google_token_for_this_app_signs_in(client, db, google):
    r = client.post("/auth/google", json={"access_token": "good"})
    assert r.status_code == 200 and r.json()["access_token"]
    assert db.scalar(select(User).where(User.email == "victim@gmail.com")) is not None


@pytest.mark.parametrize(
    "change",
    [
        {"userinfo": {"email_verified": None}},  # flag missing
        {"userinfo": {"email_verified": False}},
        {"userinfo": {"sub": "someone-else"}},  # profile of a different account
        {"tokeninfo": {"expires_in": "0"}},
    ],
)
def test_google_unverified_mismatched_or_expired_tokens_are_refused(client, db, google, change):
    for part, values in change.items():
        for k, v in values.items():
            if v is None:
                google[part].pop(k, None)
            else:
                google[part][k] = v
    assert client.post("/auth/google", json={"access_token": "t"}).status_code == 401
    assert db.scalar(select(User).where(User.email == "victim@gmail.com")) is None


def test_id_token_path_requires_explicit_verification():
    assert google_auth._is_verified(True) and google_auth._is_verified("true")
    assert not google_auth._is_verified(None) and not google_auth._is_verified(False)


# --- job links -------------------------------------------------------------------------------------


def test_ingest_rejects_non_web_job_links(db):
    from app.services.job_ingest import upsert_scraped_jobs
    from job_scanner.models import ScrapedJob

    jobs = [
        ScrapedJob(source="linkedin", external_id=None, title="T", company="C", url=u)
        for u in ("javascript:alert(document.cookie)", "data:text/html,<b>x</b>", "/jobs/view/1", "https://linkedin.com/jobs/view/42")
    ]
    counts = upsert_scraped_jobs(db, jobs)
    assert counts["inserted"] == 1
    from app.models import Job

    assert [j.url for j in db.scalars(select(Job))] == ["https://linkedin.com/jobs/view/42"]


# --- per-user cost limits -----------------------------------------------------------------------------


def test_find_matches_is_capped_per_user(client, db, monkeypatch):
    from app.api import matches as matches_api

    calls = []
    monkeypatch.setattr(matches_api, "deep_score_matches", lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(RuntimeError("stop")))
    me, other = make_user(db), make_user(db, email="other@jobagent.dev")
    for _ in range(rl.SCORE_USER.limit):
        with pytest.raises(RuntimeError):
            client.post("/matches/score", headers=auth(me), json={})
    blocked = client.post("/matches/score", headers=auth(me), json={})
    assert blocked.status_code == 429 and "Retry-After" in blocked.headers
    assert len(calls) == rl.SCORE_USER.limit  # Gemini never reached once capped
    with pytest.raises(RuntimeError):  # another user is unaffected
        client.post("/matches/score", headers=auth(other), json={})


def test_test_alerts_are_capped(client, db, monkeypatch):
    from app.api import notifications as notif_api

    monkeypatch.setattr(notif_api, "send_test_notification", lambda uid: {"status": "ok"})
    me = make_user(db)
    codes = [client.post("/notifications/test", headers=auth(me)).status_code for _ in range(rl.TEST_NOTIFY_USER.limit + 1)]
    assert codes == [200] * rl.TEST_NOTIFY_USER.limit + [429]


def test_tailor_and_resume_parse_are_capped(client, db, monkeypatch):
    from app.api import matches as matches_api
    from app.api import resumes as resumes_api
    from app.models import Resume

    monkeypatch.setattr(rl, "TAILOR_USER", rl.Limit("tailor-user", 2, 3600))
    monkeypatch.setattr(rl, "PARSE_USER", rl.Limit("parse-user", 2, 3600))
    monkeypatch.setattr(matches_api, "tailor_application_resume", lambda *a: (_ for _ in ()).throw(RuntimeError("stop")))
    monkeypatch.setattr(resumes_api, "parse_resume_text", lambda text: (_ for _ in ()).throw(RuntimeError("stop")))
    me = make_user(db)
    app = make_app(db, me)
    res = Resume(user_id=me.id, filename="cv.pdf", raw_text="x", is_primary=True)
    db.add(res)
    db.commit()
    for path in (f"/matches/{app.id}/tailor", f"/resumes/{res.id}/parse"):
        for _ in range(2):
            with pytest.raises(RuntimeError):
                client.post(path, headers=auth(me))
        assert client.post(path, headers=auth(me)).status_code == 429, path


# --- recovery shortcuts are local-dev only ---------------------------------------------------------


@pytest.fixture
def no_channels(monkeypatch):
    from app.api import auth as auth_api

    monkeypatch.setattr(auth_api, "_try_send_email", lambda **kw: "skipped")
    monkeypatch.setattr(auth_api, "_try_send_whatsapp", lambda **kw: "skipped")


@pytest.mark.parametrize(
    "env,debug,ip,shows",
    [
        ("production", False, "127.0.0.1", False),
        ("development", False, "127.0.0.1", False),  # debug off
        ("development", True, "8.8.8.8", False),  # public caller
        ("staging", True, "127.0.0.1", False),  # anything but explicit development
        ("development", True, "172.18.0.1", True),  # local Docker
    ],
)
def test_recovery_shortcuts_only_in_local_development(client, db, monkeypatch, no_channels, env, debug, ip, shows):
    monkeypatch.setattr(settings, "app_env", env)
    monkeypatch.setattr(settings, "debug", debug)
    monkeypatch.setattr(rl, "client_ip", lambda request: ip)
    make_user(db, email="someone@jobagent.dev", phone="+919876543210")

    reset = client.post("/auth/forgot-password", json={"email": "someone@jobagent.dev"}).json()
    assert bool(reset.get("reset_token")) is shows
    who = client.post("/auth/forgot-username", json={"phone": "9876543210"}).json()
    assert (who.get("login_email") == "someone@jobagent.dev") is shows
    if not shows:
        assert "someone@jobagent.dev" not in str(who)


# --- deploy check --------------------------------------------------------------------------------------


@pytest.mark.parametrize("secret", ["change-me-in-production-jobagent-dev-secret", "short-secret"])
def test_weak_jwt_secret_blocks_production_deploys(monkeypatch, capsys, secret):
    from app import preflight
    from conftest import TEST_DATABASE_URL

    monkeypatch.setattr(settings, "database_url", TEST_DATABASE_URL)
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "jwt_secret_key", secret)
    assert preflight.main() == 1
    assert "JWT_SECRET_KEY" in capsys.readouterr().out
    monkeypatch.setattr(settings, "jwt_secret_key", "x" * 48)
    assert preflight.main() == 0
