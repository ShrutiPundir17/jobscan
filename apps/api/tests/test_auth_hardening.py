from __future__ import annotations

import fakeredis
import pytest
from pydantic import ValidationError

from app.api import auth as auth_api
from app.models import PasswordResetToken, User
from app.schemas.auth import ResetPasswordRequest
from app.security import create_access_token
from app.services import rate_limit as rl
from test_auto_apply import make_user

PASSWORD = "Secret123!"  # make_user's password


@pytest.fixture(autouse=True)
def r(monkeypatch):
    client = fakeredis.FakeRedis()
    monkeypatch.setattr(rl, "redis_client", lambda: client)
    # Let tests choose the caller's IP.
    monkeypatch.setattr(rl, "client_ip", lambda request: request.headers.get("x-test-ip", "10.0.0.1"))
    return client


def login(client, email, password=PASSWORD, ip="10.0.0.1"):
    return client.post("/auth/login", json={"email": email, "password": password}, headers={"x-test-ip": ip})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


# --- sessions --------------------------------------------------------------------------------


def test_password_reset_revokes_existing_sessions(client, db):
    user = make_user(db)
    old = login(client, user.email).json()["access_token"]
    assert client.get("/users/me", headers=bearer(old)).status_code == 200

    raw = "r" * 40
    db.add(PasswordResetToken(user_id=user.id, token_hash=auth_api._hash_token(raw),
                              expires_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)
                              + auth_api.RESET_TOKEN_TTL))
    db.commit()
    assert client.post("/auth/reset-password", json={"token": raw, "new_password": "NewSecret456!"}).status_code == 200

    gone = client.get("/users/me", headers=bearer(old))
    assert gone.status_code == 401 and "sign in again" in gone.json()["detail"]
    assert login(client, user.email).status_code == 401  # old password
    new = login(client, user.email, "NewSecret456!").json()["access_token"]
    assert client.get("/users/me", headers=bearer(new)).status_code == 200


def test_logout_all_revokes_every_token(client, db):
    user = make_user(db)
    a = login(client, user.email).json()["access_token"]
    b = login(client, user.email, ip="10.0.0.2").json()["access_token"]
    assert client.post("/auth/logout-all", headers=bearer(a)).status_code == 200
    assert client.get("/users/me", headers=bearer(a)).status_code == 401
    assert client.get("/users/me", headers=bearer(b)).status_code == 401
    assert client.get("/users/me", headers=bearer(login(client, user.email).json()["access_token"])).status_code == 200


def test_tokens_from_before_this_change_keep_working_until_revoked(client, db):
    user = make_user(db)
    legacy = __import__("jwt").encode(
        {"sub": str(user.id), "exp": 9_999_999_999}, auth_api.settings.jwt_secret_key, algorithm="HS256"
    )
    assert client.get("/users/me", headers=bearer(legacy)).status_code == 200  # no "sv" = version 0
    user.session_version = 1
    db.commit()
    assert client.get("/users/me", headers=bearer(legacy)).status_code == 401


# --- brute force -------------------------------------------------------------------------------


def test_failed_logins_are_limited_per_email_and_ip(client, db):
    user = make_user(db)
    for _ in range(rl.LOGIN_FAIL_PAIR.limit):
        assert login(client, user.email, "wrong").status_code == 401
    blocked = login(client, user.email)  # even the right password waits now
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) > 0
    # The real owner on another network is not locked out by the attacker.
    assert login(client, user.email, ip="203.0.113.9").status_code == 200


def test_successful_login_resets_the_pair_counter(client, db):
    user = make_user(db)
    for _ in range(rl.LOGIN_FAIL_PAIR.limit - 1):
        login(client, user.email, "wrong")
    assert login(client, user.email).status_code == 200
    for _ in range(rl.LOGIN_FAIL_PAIR.limit - 1):
        assert login(client, user.email, "wrong").status_code == 401


def test_password_spraying_from_one_ip_is_limited(client, db):
    for i in range(rl.LOGIN_FAIL_IP.limit):
        assert login(client, f"user{i}@jobagent.dev", "guess").status_code == 401
    assert login(client, "someone.else@jobagent.dev", "guess").status_code == 429


def test_unknown_email_still_costs_a_password_check(client, db, monkeypatch):
    calls = []
    real = auth_api.verify_password
    monkeypatch.setattr(auth_api, "verify_password", lambda p, h: calls.append(h) or real(p, h))
    assert login(client, "nobody@jobagent.dev", "whatever").status_code == 401
    assert calls == [auth_api._DUMMY_PASSWORD_HASH]


def test_over_long_passwords(client, db):
    with pytest.raises(ValidationError):
        ResetPasswordRequest(token="t" * 30, new_password="é" * 40)  # 80 bytes
    user = make_user(db)
    assert login(client, user.email, PASSWORD + "x" * 80).status_code == 401  # not a 500, not a match


def test_login_works_when_redis_is_down(client, db, monkeypatch):
    class Down:
        def __getattr__(self, name):
            raise ConnectionError("redis down")

    monkeypatch.setattr(rl, "redis_client", lambda: Down())
    user = make_user(db)
    assert login(client, user.email, "wrong").status_code == 401
    assert login(client, user.email).status_code == 200


# --- recovery spam ------------------------------------------------------------------------------


def test_forgot_password_is_limited_without_revealing_accounts(client, db):
    make_user(db, email="real@jobagent.dev")
    for email in ("real@jobagent.dev", "ghost@jobagent.dev"):
        codes = [
            client.post("/auth/forgot-password", json={"email": email}, headers={"x-test-ip": f"10.1.{i}.1"}).status_code
            for i in range(rl.FORGOT_TARGET.limit + 1)
        ]
        # Same pattern for a real and a non-existent account.
        assert codes == [200] * rl.FORGOT_TARGET.limit + [429], (email, codes)


def test_forgot_username_is_limited_per_phone(client, db):
    make_user(db, phone="+919876543210")
    codes = [
        client.post("/auth/forgot-username", json={"phone": "98765 43210"}, headers={"x-test-ip": f"10.2.{i}.1"}).status_code
        for i in range(rl.FORGOT_TARGET.limit + 1)
    ]
    assert codes[-1] == 429 and codes[:-1] == [200] * rl.FORGOT_TARGET.limit


def test_rate_limit_keys_do_not_store_identities_in_clear(r, client, db):
    login(client, "private.person@jobagent.dev", "wrong")
    keys = [k.decode() for k in r.keys("*")]
    assert keys and not any("private.person" in k or "10.0.0.1" in k for k in keys)


def test_token_carries_session_version():
    import jwt

    token = create_access_token(subject="u", session_version=3)
    assert jwt.decode(token, options={"verify_signature": False})["sv"] == 3
