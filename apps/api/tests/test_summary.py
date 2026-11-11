from __future__ import annotations

from datetime import UTC, datetime

from uuid import uuid4

from app.models import ApplicationStatus, Job
from test_auto_apply import auth, make_app, make_user


def add(db, user, n, status, *, location="Pune", **kw):
    for _ in range(n):
        job = Job(source="linkedin", title="Backend Engineer", company="Acme",
                  url=f"https://jobs.example/{uuid4()}", location=location)
        db.add(job)
        db.commit()
        make_app(db, user, job=job, status=status, **kw)


def test_summary_counts_beyond_the_first_page(client, db):
    me = make_user(db, preferred_locations=["Pune"])
    other = make_user(db, email="other@jobagent.dev")
    add(db, me, 25, ApplicationStatus.pending_review)
    add(db, me, 3, ApplicationStatus.pending_review, location="Chennai")  # outside my locations
    add(db, me, 30, ApplicationStatus.applied)
    add(db, me, 2, ApplicationStatus.applying)
    add(db, me, 4, ApplicationStatus.interviewing)
    add(db, me, 1, ApplicationStatus.offered)
    add(db, other, 7, ApplicationStatus.applied)  # never counted for me

    body = client.get("/applications/summary", headers=auth(me)).json()
    assert body["to_review"] == 25  # same location filter as the Matches page
    assert body["awaiting_response"] == 32  # applied + applying, interviews not double-counted
    assert body["interviewing"] == 4 and body["offered"] == 1
    assert body["total"] == 65
    assert body["by_status"]["pending_review"] == 28

    matches = client.get("/matches?limit=100", headers=auth(me)).json()
    assert matches["total"] == 25 + 30 + 2 + 4 + 1  # every scored application in my locations


def test_summary_auto_apply_attention(client, db):
    me = make_user(db)
    now = datetime.now(UTC)
    add(db, me, 2, ApplicationStatus.pending_review, auto_apply_state="awaiting_approval", auto_apply_updated_at=now)
    add(db, me, 1, ApplicationStatus.pending_review, auto_apply_state="unconfirmed", auto_apply_updated_at=now)
    add(db, me, 1, ApplicationStatus.pending_review, auto_apply_state="sending", auto_apply_updated_at=now)
    add(db, me, 3, ApplicationStatus.pending_review, auto_apply_state="needs_manual", auto_apply_updated_at=now)
    aa = client.get("/applications/summary", headers=auth(me)).json()["auto_apply"]
    # An in-flight send is not something the user must act on.
    assert aa == {"awaiting_approval": 2, "unconfirmed": 1, "needs_manual": 3}


def test_summary_route_is_not_shadowed_and_needs_auth(client, db):
    me = make_user(db)
    assert client.get("/applications/summary").status_code in (401, 403)
    r = client.get("/applications/summary", headers=auth(me))
    assert r.status_code == 200 and r.json()["total"] == 0


def test_applications_can_be_paged_completely(client, db):
    me = make_user(db)
    add(db, me, 130, ApplicationStatus.applied)
    h = auth(me)
    seen = set()
    for offset in (0, 100):
        page = client.get(f"/applications?limit=100&offset={offset}", headers=h).json()
        assert page["total"] == 130
        seen |= {item["id"] for item in page["items"]}
    assert len(seen) == 130
