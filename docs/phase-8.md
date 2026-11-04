# Phase 8 — Auto-apply agent (ATS forms, human approval)

The agent fills public **Greenhouse, Lever and Ashby** application forms for a match.
**Nothing is submitted until the user clicks Approve & send.**

Jobs that only accept applications from a logged-in portal account (Naukri,
Internshala, Foundit, Unstop, LinkedIn Easy Apply) stay manual — no portal
passwords are stored and LinkedIn's automation ban is respected.

## Flow

```
Match card "Auto-apply"  ─or─  user's auto-apply toggle ON + new strong match
  → POST /applications/{id}/auto-apply/prepare            state: queued
  → Celery prepare_auto_apply                              state: preparing
      find ATS link: job URL → scraped payload → JD text → LinkedIn job page
      open form in headless Chromium, fill, DO NOT submit
  → awaiting_approval   (everything required is filled, resume attached, no captcha)
    needs_manual        (required questions we can't answer truthfully / captcha)
    unsupported         (no Greenhouse/Lever/Ashby form for this job)
    failed              (job closed, form missing, agent error)

Applications drawer → review every value → "Approve & send" (+ browser confirm)
  → POST /applications/{id}/auto-apply/approve             state: submitting
  → Celery submit_auto_apply
      re-fill form; abort if any value differs from the approved plan
      click submit; success only when a confirmation page/text appears
  → submitted (application status → applied)   or   failed
```

## What the agent fills

| Field | Source |
|-------|--------|
| First / last / full name | Profile name (or parsed resume name) |
| Email | Account email |
| Phone | Profile phone (or parsed resume phone) |
| LinkedIn URL | Preferences → LinkedIn profile URL |
| Resume upload | Original uploaded file (stored in Postgres so any worker host can upload it); plain-text fallback for resumes uploaded before this phase |
| Cover letter / "Additional information" textarea | Stage 2 match pitch |

It never guesses anything else — work authorization, notice period, salary,
demographic questions, etc. are listed as "Questions only you can answer" and
the application goes to `needs_manual` with a link to finish on the ATS.

## Safety rails

- Explicit approval per application; no auto-submit path exists.
- Submit aborts if the form or profile changed since approval.
- Browser only drives known ATS hosts; an off-ATS redirect aborts.
- Visible captcha → `needs_manual`; captcha after submit → `failed`.
- `AUTO_APPLY_DAILY_LIMIT` submissions per user per rolling 24h (default 10).
- `AUTO_APPLY_ENABLED=false` is a platform kill switch.
- Celery tasks never auto-retry (a retry could double-submit).
- Manual Apply / status change / Withdraw discards any pending plan; blocked
  (409) while a submission is in flight.

## API

| Method | Path | Notes |
|--------|------|-------|
| POST | `/applications/{id}/auto-apply/prepare` | 202; 409 if already working; 400 if closed |
| POST | `/applications/{id}/auto-apply/approve` | 202; 409 if nothing awaiting; 429 at daily limit |
| POST | `/applications/{id}/auto-apply/cancel` | Discard plan; 409 while submitting |

`GET /applications` and `GET /matches` include `auto_apply_state`,
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
pip install -r requirements.txt pytest httpx
playwright install chromium
python -m pytest tests
```

## Known limits

- Real ATS forms vary; custom required questions are common, so expect a share
  of `needs_manual`. That is by design — the agent never invents answers.
- Greenhouse/Lever may show invisible reCAPTCHA/hCaptcha that blocks headless
  submits; those land in `failed` with the reason, and the user can apply manually.
