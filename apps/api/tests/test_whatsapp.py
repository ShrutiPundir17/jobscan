from __future__ import annotations

import json
import logging

import pytest

from app.config import settings
from app.services import notify_whatsapp as wa
from test_auto_apply import make_app, make_user

PHONE = "+919876543210"


class _Resp:
    def __init__(self, status=201, payload=None, text=None):
        self.status_code = status
        self._payload = payload if payload is not None else {"sid": "SM123"}
        self.text = text if text is not None else json.dumps(self._payload)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


@pytest.fixture
def twilio(monkeypatch):
    for k, v in {
        "twilio_account_sid": "AC123",
        "twilio_auth_token": "secret-token",
        "twilio_whatsapp_from": "whatsapp:+14155238886",
        "twilio_template_matches_sid": "",
        "twilio_template_password_reset_sid": "",
        "twilio_template_login_email_sid": "",
        "smtp_host": None,
        "resend_api_key": None,
    }.items():
        monkeypatch.setattr(settings, k, v)
    sent: list[dict] = []
    reply = {"resp": _Resp()}

    def fake_post(url, data=None, auth=None, timeout=None):
        sent.append({"url": url, "data": dict(data), "auth": auth})
        return reply["resp"]

    monkeypatch.setattr(wa.requests, "post", fake_post)
    return {"sent": sent, "reply": reply, "monkeypatch": monkeypatch}


def test_free_form_without_template(twilio):
    assert wa.send_whatsapp(to_phone="98765 43210", body="hello\nthere", kind="matches_found", variables=[1]) == "sent"
    data = twilio["sent"][0]["data"]
    assert data == {"From": "whatsapp:+14155238886", "To": "whatsapp:+919876543210", "Body": "hello\nthere"}


def test_template_used_when_configured(twilio):
    twilio["monkeypatch"].setattr(settings, "twilio_template_matches_sid", "HXabc")
    long = "x" * 500
    wa.send_whatsapp(to_phone=PHONE, body="ignored", kind="matches_found", variables=[3, "line1\n\tline2", "", long])
    data = twilio["sent"][0]["data"]
    assert "Body" not in data and data["ContentSid"] == "HXabc"
    variables = json.loads(data["ContentVariables"])
    assert variables["1"] == "3"
    assert variables["2"] == "line1 line2"  # WhatsApp rejects newlines/tabs in variables
    assert variables["3"] == "-"  # and empty variables
    assert len(variables["4"]) == 200 and variables["4"].endswith("…")


def test_unknown_kind_falls_back_to_body(twilio):
    twilio["monkeypatch"].setattr(settings, "twilio_template_matches_sid", "HXabc")
    wa.send_whatsapp(to_phone=PHONE, body="test alert", kind="something_else")
    assert twilio["sent"][0]["data"]["Body"] == "test alert"


@pytest.mark.parametrize(
    "resp,expected",
    [
        (_Resp(400, {"code": 63016, "message": "outside window"}), "failed:needs_template"),
        (_Resp(400, {"code": 21211}), "failed:invalid_number"),
        (_Resp(401, {"code": 20003}), "failed:bad_credentials"),
        (_Resp(400, {"code": 99999}), "failed:twilio_99999"),
        (_Resp(502, ValueError("not json"), text="<html>bad gateway</html>"), "failed:http_502"),
    ],
)
def test_twilio_errors_become_readable_statuses(twilio, resp, expected):
    twilio["reply"]["resp"] = resp
    assert wa.send_whatsapp(to_phone=PHONE, body="x") == expected


def test_not_configured_and_invalid_phone(twilio, monkeypatch):
    assert wa.send_whatsapp(to_phone="123", body="x") == "skipped:invalid_phone"
    monkeypatch.setattr(settings, "twilio_auth_token", None)
    assert wa.send_whatsapp(to_phone=PHONE, body="x") == "skipped"
    assert twilio["sent"] == []


def test_logs_never_contain_full_phone_numbers(twilio, caplog, monkeypatch):
    # Alembic's fileConfig (run by conftest) disables existing loggers; re-enable so this test can see leaks.
    monkeypatch.setattr(wa.logger, "disabled", False)
    caplog.set_level(logging.INFO, logger="app.services.notify_whatsapp")
    wa.send_whatsapp(to_phone=PHONE, body="x")
    twilio["reply"]["resp"] = _Resp(400, {"code": 63016})
    wa.send_whatsapp(to_phone=PHONE, body="x")
    wa.send_whatsapp(to_phone="12", body="x")
    text = caplog.text
    assert "9876543210" not in text and "***3210" in text
    assert "secret-token" not in text


def test_match_alert_uses_matches_template(twilio, db):
    from app.services.notify_matches import create_match_notification, deliver_notification

    twilio["monkeypatch"].setattr(settings, "twilio_template_matches_sid", "HXmatch")
    twilio["monkeypatch"].setattr(settings, "app_public_url", "https://jobscan-web.vercel.app")
    user = make_user(db, notify_whatsapp_enabled=True, notify_email_enabled=False)
    apps = [make_app(db, user), make_app(db, user)]
    apps[1].match_score = 91
    db.commit()
    note = create_match_notification(db, user, apps)
    db.commit()
    result = deliver_notification(db, note.id)
    assert result["whatsapp"] == "sent"
    data = twilio["sent"][0]["data"]
    assert data["ContentSid"] == "HXmatch"
    assert json.loads(data["ContentVariables"]) == {"1": "2", "2": "91", "3": "https://jobscan-web.vercel.app"}


def test_password_reset_uses_its_template(twilio, client, db):
    twilio["monkeypatch"].setattr(settings, "twilio_template_password_reset_sid", "HXreset")
    make_user(db, email="reset.me@jobagent.dev", phone=PHONE)
    r = client.post("/auth/forgot-password", json={"email": "reset.me@jobagent.dev"})
    assert r.status_code == 200 and "WhatsApp" in r.json()["message"]
    data = twilio["sent"][0]["data"]
    assert data["ContentSid"] == "HXreset"
    link = json.loads(data["ContentVariables"])["1"]
    assert "reset_token=" in link and link.startswith(settings.app_public_url)


def test_forgot_username_uses_login_email_template(twilio, client, db):
    twilio["monkeypatch"].setattr(settings, "twilio_template_login_email_sid", "HXlogin")
    make_user(db, email="find.me@jobagent.dev", phone=PHONE)
    r = client.post("/auth/forgot-username", json={"phone": "98765 43210"})
    assert r.status_code == 200
    data = twilio["sent"][0]["data"]
    assert data["ContentSid"] == "HXlogin"
    assert json.loads(data["ContentVariables"]) == {"1": "find.me@jobagent.dev"}
