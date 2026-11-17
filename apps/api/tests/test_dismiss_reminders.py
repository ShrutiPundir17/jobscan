from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models import Application, ApplicationStatus, Notification
from app.services import auto_apply as svc
from app.services.reminders import FOLLOW_UP_DUE, fire_due_reminders
from test_auto_apply import auth, make_app, make_job, make_user, reload

NOW = datetime.now(UTC)


def notes_for(db, user) -> list[Notification]:
    db.expire_all()
    return list(db.scalars(select(Notification).where(Notification.user_id == user.id, Notification.type == FOLLOW_UP_DUE)))


# --- dismiss ----------------------------------------------------------------------------------


def test_dismissed_match_is_hidden_everywhere_until_restored(client, db):
    me = make_user(db)
    keep, gone = make_app(db, me), make_app(db, me)
    h = auth(me)
    assert client.post(f"/matches/{gone.id}/dismiss", headers=h).status_code == 200

    assert [m["id"] for m in client.get("/matches", headers=h).json()["items"]] == [str(keep.id)]
    assert client.get("/applications", headers=h).json()["total"] == 1
    assert client.get("/applications?include_dismissed=true", headers=h).json()["total"] == 2
    summary = client.get("/applications/summary", headers=h).json()
    assert summary["to_review"] == 1 and summary["total"] == 1

    assert client.post(f"/matches/{gone.id}/restore", headers=h).status_code == 200
    assert client.get("/matches", headers=h).json()["total"] == 2


def test_rescoring_never_resurrects_a_dismissed_match(client, db):
    from app.models import Resume
    from app.services.match_score import _upsert_application

    me = make_user(db)
    app = make_app(db, me)
    client.post(f"/matches/{app.id}/dismiss", headers=auth(me))
    resume = Resume(user_id=me.id, filename="cv.pdf", is_primary=True)
    db.add(resume)
    db.commit()
    _upsert_application(db, user=me, resume=resume, job=reload(db, app).job, score=97,
                        reasoning="even better now", skill_gaps=[], tailored_pitch="p")
    db.commit()
    row = reload(db, app)
    assert row.dismissed_at is not None and row.match_score == 97
    assert client.get("/matches", headers=auth(me)).json()["total"] == 0


def test_auto_apply_never_prepares_dismissed_matches(db, queued):
    me = make_user(db, auto_apply_enabled=True)
    app = make_app(db, me, dismissed_at=NOW)
    assert svc.queue_auto_prepare(db, me, [str(app.id)]) == 0 and queued == []


def test_dismiss_respects_auto_apply_in_flight(client, db):
    me = make_user(db)
    h = auth(me)
    sending = make_app(db, me, auto_apply_state="submitting", auto_apply_attempt="a1", auto_apply_updated_at=NOW)
    assert client.post(f"/matches/{sending.id}/dismiss", headers=h).status_code == 409
    waiting = make_app(db, me, auto_apply_state="awaiting_approval", auto_apply_plan={"ready": True}, auto_apply_updated_at=NOW)
    assert client.post(f"/matches/{waiting.id}/dismiss", headers=h).status_code == 200
    row = reload(db, waiting)
    assert row.auto_apply_state == "dismissed" and row.auto_apply_plan is None  # can't be approved later


def test_dismiss_is_owner_only(client, db):
    owner, other = make_user(db), make_user(db, email="other@jobagent.dev")
    app = make_app(db, owner)
    assert client.post(f"/matches/{app.id}/dismiss", headers=auth(other)).status_code == 404
    assert reload(db, app).dismissed_at is None


# --- setting reminders --------------------------------------------------------------------------


def test_reminder_validation_and_clearing(client, db):
    me = make_user(db)
    app = make_app(db, me)
    h, url = auth(me), f"/applications/{app.id}"
    assert client.patch(url, headers=h, json={"follow_up_at": (NOW - timedelta(days=3)).isoformat()}).status_code == 422
    assert client.patch(url, headers=h, json={"follow_up_at": (NOW + timedelta(days=400)).isoformat()}).status_code == 422

    naive = (NOW + timedelta(days=2)).replace(tzinfo=None, microsecond=0).isoformat()
    body = client.patch(url, headers=h, json={"follow_up_at": naive}).json()
    assert body["follow_up_at"].startswith(naive) and body["follow_up_notified_at"] is None

    cleared = client.patch(url, headers=h, json={"follow_up_at": None}).json()
    assert cleared["follow_up_at"] is None
    # Notes-only updates leave the reminder alone.
    client.patch(url, headers=h, json={"follow_up_at": naive})
    assert client.patch(url, headers=h, json={"notes": "called HR"}).json()["follow_up_at"].startswith(naive)


# --- firing reminders -----------------------------------------------------------------------------


def test_due_reminder_fires_exactly_once(db):
    me = make_user(db)
    due = make_app(db, me, follow_up_at=NOW - timedelta(minutes=5), notes="Ask about the take-home")
    later = make_app(db, me, follow_up_at=NOW + timedelta(days=1))
    delivered = []
    assert fire_due_reminders(db, deliver=delivered.append) == 1
    assert fire_due_reminders(db, deliver=delivered.append) == 0  # never twice
    notes = notes_for(db, me)
    assert len(notes) == 1 and len(delivered) == 1 and delivered[0] == notes[0].id
    assert "Backend Engineer @ Acme" in notes[0].title and "take-home" in notes[0].body
    assert reload(db, due).follow_up_notified_at is not None
    assert reload(db, later).follow_up_notified_at is None


def test_closed_dismissed_and_other_users_are_skipped(db):
    me, other = make_user(db), make_user(db, email="other@jobagent.dev")
    past = NOW - timedelta(minutes=1)
    make_app(db, me, follow_up_at=past, status=ApplicationStatus.withdrawn)
    make_app(db, me, follow_up_at=past, status=ApplicationStatus.rejected)
    make_app(db, me, follow_up_at=past, dismissed_at=NOW)
    make_app(db, other, follow_up_at=past)
    assert fire_due_reminders(db, user_id=me.id) == 0
    assert fire_due_reminders(db, user_id=other.id) == 1


def test_changing_the_time_rearms_a_sent_reminder(client, db):
    me = make_user(db)
    app = make_app(db, me, follow_up_at=NOW - timedelta(minutes=1))
    fire_due_reminders(db)
    soon = (datetime.now(UTC) + timedelta(seconds=2)).isoformat()
    assert client.patch(f"/applications/{app.id}", headers=auth(me), json={"follow_up_at": soon}).json()["follow_up_notified_at"] is None
    assert fire_due_reminders(db, now=datetime.now(UTC) + timedelta(minutes=1)) == 1
    assert len(notes_for(db, me)) == 2


def test_concurrent_sweeps_never_double_send(db):
    from app.db import SessionLocal

    me = make_user(db)
    for _ in range(5):
        make_app(db, me, follow_up_at=NOW - timedelta(minutes=1))
    barrier = threading.Barrier(4)
    counts: list[int] = []

    def sweep():
        s = SessionLocal()
        try:
            barrier.wait()
            counts.append(fire_due_reminders(s))
        finally:
            s.close()

    threads = [threading.Thread(target=sweep) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(counts) == 5 and len(notes_for(db, me)) == 5


def test_fetching_notifications_fires_due_reminders(client, db, monkeypatch):
    from app.tasks import notify_matches

    queued = []
    monkeypatch.setattr(notify_matches.deliver_notification_task, "delay", lambda nid: queued.append(nid))
    me = make_user(db)
    make_app(db, me, follow_up_at=NOW - timedelta(minutes=1))
    body = client.get("/notifications?unread_only=true", headers=auth(me)).json()
    assert body["unread_count"] == 1 and body["items"][0]["type"] == FOLLOW_UP_DUE
    assert len(queued) == 1  # email/WhatsApp go to the background, not the request


def test_github_actions_scan_fires_and_delivers_reminders(db, monkeypatch):
    from app import scan_once
    from app.services import notify_matches as nm

    sent = []
    monkeypatch.setattr(nm, "deliver_notification", lambda s, nid: sent.append(nid) or {"email": "skipped"})
    me = make_user(db)
    make_app(db, me, follow_up_at=NOW - timedelta(minutes=1))
    assert scan_once.fire_reminders_now() == 1 and len(sent) == 1


def test_beat_runs_the_reminder_sweep():
    from app.celery_app import celery

    tasks = {entry["task"] for entry in celery.conf.beat_schedule.values()}
    assert "app.tasks.notify_matches.fire_due_reminders" in tasks
