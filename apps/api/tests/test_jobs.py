from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.config import settings
from app.models import Application, ApplicationStatus, Job
from test_auto_apply import auth, make_user

NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)


def job(db, title="Backend Engineer", company="Acme", *, source="linkedin", location="Bangalore",
        posted=None, description="Build Python APIs.", **kw) -> Job:
    j = Job(
        source=source,
        title=title,
        company=company,
        location=location,
        url=f"https://example.com/{uuid4()}",
        description=description,
        posted_at=posted,
        raw_payload={"internal": "do not leak", "search_location": location},
        embedding=[0.02] * settings.embedding_dimensions,
        **kw,
    )
    db.add(j)
    db.commit()
    return j


def titles(resp) -> list[str]:
    return [i["title"] for i in resp.json()["items"]]


def test_requires_auth(client):
    assert client.get("/jobs").status_code in (401, 403)
    assert client.get(f"/jobs/{uuid4()}").status_code in (401, 403)


def test_newest_first_and_nothing_internal_leaks(client, db):
    user = make_user(db)
    job(db, "Old", posted=NOW - timedelta(days=5))
    job(db, "New", posted=NOW)
    job(db, "Undated")  # falls back to scrape time (now) -> newest
    r = client.get("/jobs", headers=auth(user))
    assert r.status_code == 200
    assert titles(r) == ["Undated", "New", "Old"]
    item = r.json()["items"][0]
    assert "raw_payload" not in item and "embedding" not in item and "description" not in item
    assert r.json()["total"] == 3


def test_pagination_is_stable_with_tied_timestamps(client, db):
    user = make_user(db)
    for i in range(7):
        job(db, f"Job {i}", posted=NOW)  # identical sort keys
    h = auth(user)
    seen: list[str] = []
    for offset in (0, 3, 6):
        seen += [i["id"] for i in client.get(f"/jobs?limit=3&offset={offset}", headers=h).json()["items"]]
    assert len(seen) == 7 and len(set(seen)) == 7  # no duplicates, nothing skipped
    assert client.get("/jobs?offset=50", headers=h).json()["items"] == []


def test_search_tokens_match_across_fields(client, db):
    user = make_user(db)
    job(db, "Python Developer", "Acme", location="Pune")
    job(db, "Python Developer", "Globex", location="Bangalore")
    job(db, "Java Developer", "Acme", location="Bangalore")
    h = auth(user)
    assert titles(client.get("/jobs?q=python", headers=h)) == ["Python Developer", "Python Developer"]
    r = client.get("/jobs?q=PYTHON acme", headers=h)
    assert [(i["title"], i["company"]) for i in r.json()["items"]] == [("Python Developer", "Acme")]
    assert client.get("/jobs?q=python bangalore", headers=h).json()["total"] == 1
    assert client.get("/jobs?q=%20%20", headers=h).json()["total"] == 3  # blank query = no filter


def test_search_wildcards_are_literal(client, db):
    user = make_user(db)
    job(db, "Data Engineer")
    job(db, "C++ Developer_II")
    job(db, "100% Remote Engineer")
    h = auth(user)
    assert client.get("/jobs?q=%25", headers=h).json()["total"] == 1  # "%" only matches the literal %
    assert titles(client.get("/jobs?q=_", headers=h)) == ["C++ Developer_II"]
    assert client.get("/jobs?q=%5C", headers=h).json()["total"] == 0  # backslash is not an escape trick


def test_filters_and_validation(client, db):
    user = make_user(db, preferred_locations=["Pune"])
    job(db, "A", source="naukri", location="Pune, Maharashtra")
    job(db, "B", source="linkedin", location="Bangalore")
    job(db, "C", source="unstop", location="Chennai")
    h = auth(user)
    assert titles(client.get("/jobs?source=NAUKRI", headers=h)) == ["A"]
    assert titles(client.get("/jobs?location=bangal", headers=h)) == ["B"]
    assert titles(client.get("/jobs?my_locations=true", headers=h)) == ["A"]
    assert client.get("/jobs?source=link'edin", headers=h).status_code == 422
    assert client.get("/jobs?q=" + "x" * 101, headers=h).status_code == 422
    assert client.get("/jobs?limit=101", headers=h).status_code == 422
    assert client.get("/jobs?limit=0", headers=h).status_code == 422


def test_my_locations_without_preferences_is_no_filter(client, db):
    user = make_user(db)
    job(db, "A")
    assert client.get("/jobs?my_locations=true", headers=auth(user)).json()["total"] == 1


def test_my_application_is_only_mine(client, db):
    me = make_user(db)
    other = make_user(db, email="other@jobagent.dev")
    shared = job(db, "Shared")
    mine_only = job(db, "Mine")
    job(db, "Nobody's")
    db.add_all([
        Application(user_id=me.id, job_id=mine_only.id, status=ApplicationStatus.applied, match_score=88),
        Application(user_id=other.id, job_id=shared.id, status=ApplicationStatus.pending_review, match_score=91),
    ])
    db.commit()

    items = {i["title"]: i for i in client.get("/jobs", headers=auth(me)).json()["items"]}
    assert len(items) == 3  # the join never duplicates or drops jobs
    assert items["Mine"]["my_application"]["status"] == "applied"
    assert items["Mine"]["my_application"]["match_score"] == 88
    assert items["Mine"]["my_application"]["verdict"] == "strong"
    assert items["Shared"]["my_application"] is None  # other user's data never shows
    assert items["Nobody's"]["my_application"] is None

    detail = client.get(f"/jobs/{shared.id}", headers=auth(other)).json()
    assert detail["my_application"]["match_score"] == 91
    assert client.get(f"/jobs/{shared.id}", headers=auth(me)).json()["my_application"] is None


def test_detail_and_snippet(client, db):
    user = make_user(db)
    long = "Build   Python\nAPIs. " + "word " * 200
    j = job(db, "Long JD", description=long)
    h = auth(user)
    item = client.get("/jobs", headers=h).json()["items"][0]
    assert item["description_snippet"].startswith("Build Python APIs.")
    assert len(item["description_snippet"]) <= 281 and item["description_snippet"].endswith("…")
    d = client.get(f"/jobs/{j.id}", headers=h)
    assert d.status_code == 200
    assert d.json()["description"] == long
    assert "raw_payload" not in d.json() and "embedding" not in d.json()
    assert client.get(f"/jobs/{uuid4()}", headers=h).status_code == 404
    assert client.get("/jobs/not-a-uuid", headers=h).status_code == 422


def test_short_and_missing_descriptions(client, db):
    user = make_user(db)
    job(db, "Short", description="  Tiny JD.  ")
    job(db, "None", description=None)
    snippets = {i["title"]: i["description_snippet"] for i in client.get("/jobs", headers=auth(user)).json()["items"]}
    assert snippets == {"Short": "Tiny JD.", "None": None}
