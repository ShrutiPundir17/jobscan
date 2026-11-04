from __future__ import annotations

import io
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.models import Application, ApplicationStatus, Job, Resume, User
from app.services import auto_apply as svc
from app.services import resume_admin
from test_auto_apply import auth, make_app, make_user

VEC = [0.01] * settings.embedding_dimensions


def make_resume(db, user, name="cv.pdf", *, primary=False, embedded=True, created=None, on_disk=False) -> Resume:
    path = None
    if on_disk:
        root = Path(settings.upload_dir) / str(user.id)
        root.mkdir(parents=True, exist_ok=True)
        path = root / name
        path.write_bytes(b"%PDF-1.4 on disk")
    r = Resume(
        user_id=user.id,
        filename=name,
        storage_path=str(path) if path else None,
        file_data=b"%PDF-1.4 " + name.encode(),
        raw_text=f"text of {name}",
        parsed_data={"full_name": "Shruti Pundir"} if embedded else None,
        is_primary=primary,
        embedding=VEC if embedded else None,
        embedded_at=datetime.now(UTC) if embedded else None,
    )
    if created is not None:
        r.created_at = created
    db.add(r)
    db.commit()
    return r


def primaries(db, user) -> list[str]:
    db.expire_all()
    return [r.filename for r in db.scalars(select(Resume).where(Resume.user_id == user.id, Resume.is_primary))]


# --- invariant --------------------------------------------------------------------


def test_db_rejects_two_primaries(db):
    user = make_user(db)
    make_resume(db, user, "a.pdf", primary=True)
    with pytest.raises(IntegrityError):
        make_resume(db, user, "b.pdf", primary=True)
    db.rollback()
    other = make_user(db, email="other@jobagent.dev")
    make_resume(db, other, "c.pdf", primary=True)  # per user, not global


def test_migration_normalizes_existing_data(db):
    from alembic import command
    from alembic.config import Config

    from conftest import API_ROOT

    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    command.downgrade(cfg, "0010_auto_apply")
    try:
        two = make_user(db, email="two@jobagent.dev")
        old = datetime.now(UTC) - timedelta(days=2)
        make_resume(db, two, "old.pdf", primary=True)
        db.execute(text("UPDATE resumes SET updated_at = :t WHERE filename = 'old.pdf'"), {"t": old})
        make_resume(db, two, "new.pdf", primary=True)
        none = make_user(db, email="none@jobagent.dev")
        make_resume(db, none, "unembedded-newer.pdf", embedded=False)
        make_resume(db, none, "embedded-older.pdf", created=old)
        db.commit()
    finally:
        command.upgrade(cfg, "head")
    assert primaries(db, two) == ["new.pdf"]
    assert primaries(db, none) == ["embedded-older.pdf"]


def test_concurrent_uploads_make_exactly_one_primary(client, db, monkeypatch):
    from app.api import resumes as resumes_api

    def slow_extract(**kw):
        time.sleep(0.4)  # both requests reach the primary decision together
        return "resume text"

    monkeypatch.setattr(resumes_api, "extract_resume_text", slow_extract)
    user = make_user(db)
    h = auth(user)
    codes: list[int] = []

    def upload(name):
        r = client.post("/resumes/upload", headers=h, files={"file": (name, io.BytesIO(b"%PDF-1.4"), "application/pdf")})
        codes.append(r.status_code)

    threads = [threading.Thread(target=upload, args=(f"cv{i}.pdf",)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert codes == [201, 201, 201]
    assert len(primaries(db, user)) == 1


def test_upload_failure_leaves_no_orphan_file(client, db, monkeypatch):
    from app.api import resumes as resumes_api

    monkeypatch.setattr(resumes_api, "extract_resume_text", lambda **kw: "text")

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(resume_admin, "lock_user", boom)
    user = make_user(db)
    with pytest.raises(RuntimeError):
        client.post("/resumes/upload", headers=auth(user), files={"file": ("x.pdf", io.BytesIO(b"%PDF"), "application/pdf")})
    user_dir = Path(settings.upload_dir) / str(user.id)
    assert not user_dir.exists() or not any(user_dir.iterdir())


def test_response_does_not_leak_server_paths(client, db):
    user = make_user(db)
    make_resume(db, user, primary=True, on_disk=True)
    body = client.get("/resumes", headers=auth(user)).json()["items"][0]
    assert "storage_path" not in body and "file_data" not in body


# --- set primary ------------------------------------------------------------------------


def test_set_primary_switches_and_is_idempotent(client, db):
    user = make_user(db)
    a = make_resume(db, user, "a.pdf", primary=True)
    b = make_resume(db, user, "b.pdf")
    h = auth(user)
    r = client.post(f"/resumes/{b.id}/set-primary", headers=h)
    assert r.status_code == 200 and r.json()["is_primary"] is True
    assert primaries(db, user) == ["b.pdf"]
    assert client.post(f"/resumes/{b.id}/set-primary", headers=h).status_code == 200
    assert primaries(db, user) == ["b.pdf"]
    client.post(f"/resumes/{a.id}/set-primary", headers=h)
    assert primaries(db, user) == ["a.pdf"]


def test_set_primary_requires_embedding(client, db):
    user = make_user(db)
    make_resume(db, user, "a.pdf", primary=True)
    raw = make_resume(db, user, "raw.pdf", embedded=False)
    r = client.post(f"/resumes/{raw.id}/set-primary", headers=auth(user))
    assert r.status_code == 409 and "Parse" in r.json()["detail"]
    assert primaries(db, user) == ["a.pdf"]


def test_set_primary_other_users_resume_is_404(client, db):
    owner = make_user(db)
    other = make_user(db, email="other@jobagent.dev")
    r = make_resume(db, owner, primary=True)
    assert client.post(f"/resumes/{r.id}/set-primary", headers=auth(other)).status_code == 404


def test_matching_follows_new_primary(client, db):
    user = make_user(db)
    make_resume(db, user, "a.pdf", primary=True)
    b = make_resume(db, user, "b.pdf")
    h = auth(user)
    client.post(f"/resumes/{b.id}/set-primary", headers=h)
    r = client.post("/matches/search", headers=h, json={"limit": 5})
    assert r.status_code == 200, r.text
    assert r.json()["resume_id"] == str(b.id)


# --- delete -------------------------------------------------------------------------------


def test_delete_non_primary_keeps_primary_and_removes_file(client, db):
    user = make_user(db)
    make_resume(db, user, "keep.pdf", primary=True)
    gone = make_resume(db, user, "gone.pdf", on_disk=True)
    gone_id, path = gone.id, Path(gone.storage_path)
    assert path.exists()
    h = auth(user)
    r = client.delete(f"/resumes/{gone_id}", headers=h)
    assert r.status_code == 200
    assert r.json() == {"id": str(gone_id), "new_primary_id": None, "retired_auto_apply_plans": 0}
    assert not path.exists()
    assert primaries(db, user) == ["keep.pdf"]
    assert client.get(f"/resumes/{gone_id}", headers=h).status_code == 404
    assert client.delete(f"/resumes/{gone_id}", headers=h).status_code == 404


def test_delete_primary_promotes_embedded_before_newer_unembedded(client, db):
    user = make_user(db)
    old = datetime.now(UTC) - timedelta(days=3)
    embedded = make_resume(db, user, "embedded-old.pdf", created=old)
    make_resume(db, user, "raw-new.pdf", embedded=False)
    primary = make_resume(db, user, "primary.pdf", primary=True)
    r = client.delete(f"/resumes/{primary.id}", headers=auth(user))
    assert r.json()["new_primary_id"] == str(embedded.id)
    assert primaries(db, user) == ["embedded-old.pdf"]


def test_delete_last_resume_stops_matching_cleanly(client, db):
    user = make_user(db)
    only = make_resume(db, user, primary=True)
    h = auth(user)
    assert client.delete(f"/resumes/{only.id}", headers=h).json()["new_primary_id"] is None
    r = client.post("/matches/search", headers=h, json={"limit": 5})
    assert r.status_code == 400 and "Upload" in r.json()["detail"]


def test_delete_other_users_resume_is_404(client, db):
    owner = make_user(db)
    other = make_user(db, email="other@jobagent.dev")
    r = make_resume(db, owner, primary=True)
    assert client.delete(f"/resumes/{r.id}", headers=auth(other)).status_code == 404
    assert primaries(db, owner) == ["cv.pdf"]


def test_delete_keeps_applications_and_clears_their_link(client, db):
    user = make_user(db)
    r = make_resume(db, user, primary=True)
    app = make_app(db, user, resume_id=r.id)
    client.delete(f"/resumes/{r.id}", headers=auth(user))
    db.expire_all()
    assert db.get(Application, app.id).resume_id is None
    assert client.get("/applications", headers=auth(user)).json()["total"] == 1


def _plan(resume_id, **extra):
    return {
        "ready": True,
        "filled": [],
        "unanswered_required": [],
        "resume_id": str(resume_id),
        "applicant": {"resume_id": str(resume_id)},
        "prepared_at": datetime.now(UTC).isoformat(),
        **extra,
    }


def test_delete_retires_waiting_plans_but_keeps_history(client, db):
    user = make_user(db)
    gone = make_resume(db, user, "gone.pdf", primary=True)
    other = make_resume(db, user, "other.pdf")
    now = datetime.now(UTC)
    waiting = make_app(db, user, auto_apply_state="awaiting_approval", auto_apply_plan=_plan(gone.id), auto_apply_updated_at=now)
    manual = make_app(db, user, auto_apply_state="needs_manual", auto_apply_plan=_plan(gone.id), auto_apply_updated_at=now)
    unconfirmed = make_app(db, user, auto_apply_state="unconfirmed", auto_apply_plan=_plan(gone.id), auto_apply_updated_at=now)
    submitted = make_app(
        db, user, status=ApplicationStatus.applied, applied_at=now,
        auto_apply_state="submitted", auto_apply_plan=_plan(gone.id), auto_apply_updated_at=now,
    )
    unrelated = make_app(db, user, auto_apply_state="awaiting_approval", auto_apply_plan=_plan(other.id), auto_apply_updated_at=now)

    r = client.delete(f"/resumes/{gone.id}", headers=auth(user))
    assert r.json()["retired_auto_apply_plans"] == 2
    db.expire_all()
    for a in (waiting, manual):
        row = db.get(Application, a.id)
        assert row.auto_apply_state == "failed" and row.auto_apply_plan is None
        assert row.auto_apply_error == resume_admin.RESUME_GONE_ERROR
    assert db.get(Application, unconfirmed.id).auto_apply_state == "unconfirmed"
    assert db.get(Application, submitted.id).auto_apply_state == "submitted"
    assert db.get(Application, unrelated.id).auto_apply_state == "awaiting_approval"
    # The retired ones can be prepared again.
    h = auth(user)
    assert client.get(f"/applications/{waiting.id}", headers=h).json()["auto_apply_state"] == "failed"


@pytest.mark.parametrize("state", ["submitting", "sending"])
def test_delete_blocked_while_submission_in_flight(client, db, state):
    user = make_user(db)
    r = make_resume(db, user, primary=True)
    make_app(db, user, auto_apply_state=state, auto_apply_attempt="a1", auto_apply_plan=_plan(r.id), auto_apply_updated_at=datetime.now(UTC))
    resp = client.delete(f"/resumes/{r.id}", headers=auth(user))
    assert resp.status_code == 409 and "in progress" in resp.json()["detail"]
    db.expire_all()
    assert db.get(Resume, r.id) is not None


def test_approve_refuses_plan_whose_resume_vanished(client, db, queued):
    user = make_user(db)
    r = make_resume(db, user, primary=True)
    app = make_app(db, user, auto_apply_state="awaiting_approval", auto_apply_plan=_plan(r.id), auto_apply_updated_at=datetime.now(UTC))
    # Bypass the endpoint (as a race would): the row disappears without retiring the plan.
    db.execute(text("DELETE FROM resumes WHERE id = :id"), {"id": r.id})
    db.commit()
    resp = client.post(f"/applications/{app.id}/auto-apply/approve", headers=auth(user))
    assert resp.status_code == 409 and "deleted" in resp.json()["detail"]
    assert queued == []


def test_remove_stored_file_refuses_paths_outside_upload_dir(tmp_path):
    outside = tmp_path / "important.txt"
    outside.write_text("keep me")
    resume_admin.remove_stored_file(str(outside))
    assert outside.exists()
    resume_admin.remove_stored_file(str(Path(settings.upload_dir) / ".." / "escape.txt"))
    resume_admin.remove_stored_file(None)


def test_upload_limits_still_enforced(client, db, monkeypatch):
    from app.api import resumes as resumes_api

    monkeypatch.setattr(resumes_api, "extract_resume_text", lambda **kw: "text")
    monkeypatch.setattr(settings, "max_upload_bytes", 10)
    user = make_user(db)
    h = auth(user)
    big = client.post("/resumes/upload", headers=h, files={"file": ("a.pdf", io.BytesIO(b"x" * 11), "application/pdf")})
    assert big.status_code == 413
    empty = client.post("/resumes/upload", headers=h, files={"file": ("a.pdf", io.BytesIO(b""), "application/pdf")})
    assert empty.status_code == 400
    exe = client.post("/resumes/upload", headers=h, files={"file": ("a.exe", io.BytesIO(b"x"), "application/octet-stream")})
    assert exe.status_code == 400
    ok = client.post("/resumes/upload", headers=h, files={"file": ("a.pdf", io.BytesIO(b"x" * 10), "application/pdf")})
    assert ok.status_code == 201 and ok.json()["is_primary"] is True
