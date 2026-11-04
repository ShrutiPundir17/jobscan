# Phase 8 — Auto-apply agent (ATS forms, human approval)

The agent fills public **Greenhouse, Lever and Ashby** application forms for a match.
**Nothing is submitted until the user clicks Approve & send.**

Jobs that only accept applications from a logged-in portal account (Naukri,
Internshala, Foundit, Unstop, LinkedIn Easy Apply) stay manual — no portal
passwords are stored and LinkedIn's automation ban is respected.

## Flow

```
Match card "Auto-apply"  ─or─  auto-apply toggle ON + new strong match (state is empty)
  → POST /applications/{id}/auto-apply/prepare            state: queued      (task gets an attempt id)
  → Celery prepare_auto_apply                              state: preparing   (atomic claim on state+attempt)
      find ATS link: job URL → scraped payload → JD (only if it has exactly one ATS link)
                     → LinkedIn job page (only its <code id="applyUrl"> element)
      DRY RUN: every non-GET request from the page is blocked and the resume is
      never attached, so nothing reaches the employer
  → awaiting_approval   (everything required is filled, resume fits the form, no captcha)
    needs_manual        (required questions we can't answer truthfully / captcha / file type)
    unsupported         (no Greenhouse/Lever/Ashby form for this job)
    failed              (job closed, form missing, agent error)

Applications drawer → review every value (+ form title check) → "Approve & send" (+ browser confirm)
  → POST /applications/{id}/auto-apply/approve             state: submitting  (atomic; new attempt id)
  → Celery submit_auto_apply                               state: sending     (claimed under a per-user lock + daily cap)
      fill from the approved snapshot; abort if the form would send anything else
      click the one unambiguous submit button
  → submitted    new confirmation text/URL appeared and the form is gone (status → applied)
    failed       definitely not sent (validation error on the same form, captcha, pre-click problem)
    unconfirmed  clicked but no proof either way → user answers "received?" via /resolve
```

`dismissed` = the user discarded the plan; matching never re-queues it, but the
user can start it again by hand.

## States that outlive their worker

Read-time (no background job needed): `queued` > 60 min, `preparing` > 15 min,
`submitting` > 60 min are shown as `failed` and can be retried — a late task can
no longer claim them. `sending` > 15 min is shown as `unconfirmed`, because the
click may have happened.

## What the agent fills

| Field | Source |
|-------|--------|
| First / last / full name | Profile name (or parsed resume name) |
| Email (+ "Confirm email") | Account email |
| Phone | Profile phone (or parsed resume phone) |
| LinkedIn URL | Preferences → LinkedIn profile URL |
| Resume upload | Original uploaded file (stored in Postgres so any worker host can upload it); plain-text fallback for older resumes, refused when the form's `accept` doesn't allow it |
| Cover letter / "Additional information" textarea | Stage 2 match pitch |

Labels must match as a whole ("Email", not "Referrer email"; "Name", not
"Name of current employer"). Everything else — work authorization, notice
period, salary, consent checkboxes, demographic questions — is listed as
"Questions only you can answer" and the application goes to `needs_manual`.

All values are frozen into the plan at prepare time; submit sends exactly that
snapshot even if the profile or match pitch changes later.

## Safety rails

- Explicit approval per application; no auto-submit path exists.
- One approval → at most one submission: attempt ids + compare-and-swap on every
  transition; duplicate, late or superseded tasks no-op.
- Once the click may have fired the outcome is `submitted` or `unconfirmed`, never a
  silently retryable `failed`.
- Confirmation must be *new* compared with a pre-click snapshot (job descriptions often
  say "thank you for applying"), and the form must be gone or the page must navigate.
- Prepare never sends data to the employer (writes blocked, resume not attached).
- Browser only drives known ATS hosts; an off-ATS redirect aborts.
- `AUTO_APPLY_DAILY_LIMIT` per user per rolling 24h (default 10), counting in-flight and
  unconfirmed submissions; re-checked under a Postgres advisory lock when the worker claims.
- Plans older than 7 days must be prepared again before approval.
- `AUTO_APPLY_ENABLED=false` is a platform kill switch.
- Celery tasks never auto-retry.
- Manual Apply / status change / Withdraw retires any pending plan (→ `dismissed`);
  blocked (409) while a submission is in flight.
- In-app notifications for ready / submitted / unconfirmed / failed.

## API

| Method | Path | Notes |
|--------|------|-------|
| POST | `/applications/{id}/auto-apply/prepare` | 202; 409 if working or unconfirmed; 400 if closed/submitted |
| POST | `/applications/{id}/auto-apply/approve` | 202; 409 if nothing awaiting or plan > 7 days; 429 at daily limit |
| POST | `/applications/{id}/auto-apply/cancel` | → `dismissed`; 409 while submitting or unconfirmed |
| POST | `/applications/{id}/auto-apply/resolve` | `{"received": bool}` for `unconfirmed` only |

`GET /applications` and `GET /matches` include the *effective* `auto_apply_state`,
`auto_apply_provider`, `auto_apply_error` (applications also `auto_apply_url`,
`auto_apply_plan`).

## Env

```env
AUTO_APPLY_ENABLED=true
AUTO_APPLY_DAILY_LIMIT=10
AUTO_APPLY_HEADLESS=true
AUTO_APPLY_BROWSER_CHANNEL=
AUTO_APPLY_RESOLVE_VIA_FETCH=true
```

Migration: `0010_auto_apply` (run `alembic upgrade head`).

## Tests

```powershell
# Agent: ATS detection + real headless Chromium against local mock ATS forms
cd workers/apply-agent
python -m pytest tests

# API: needs a throwaway Postgres+pgvector (tests TRUNCATE every table)
docker run -d --name jobagent-test-pg -p 55499:5432 `
  -e POSTGRES_USER=jobagent -e POSTGRES_PASSWORD=jobagent -e POSTGRES_DB=jobagent_test `
  pgvector/pgvector:pg16
cd apps/api
pip install -r requirements-dev.txt
playwright install chromium
python -m pytest tests
```

## Known limits

- Real ATS forms vary; custom required questions are common, so expect a share
  of `needs_manual`. That is by design — the agent never invents answers.
- Greenhouse/Lever may show invisible reCAPTCHA/hCaptcha that blocks headless
  submits; those land in `failed` (or `unconfirmed` if nothing visible changed).
- Celery runs one task at a time and shares the queue with portal scans, so a
  prepare can wait behind a scan. A dedicated `auto_apply` queue/worker is the
  next step if that becomes noticeable.
