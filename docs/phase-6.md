# Phase 6 — Resume tailoring (per job JD)

Rewrite experience/project bullets to align with a specific job description.

## Goal

For each persisted match, produce a JD-aware resume rewrite:

- Reframe existing bullets toward the job’s language and priorities
- Do **not** invent employers, titles, or fake achievements
- Store structured bullets + a plain-text tailored resume on the application

## API

```http
POST /matches/{application_id}/tailor
Authorization: Bearer <token>
```

Response includes:

- `tailored_bullets` — `{ summary, experience[], projects[] }`
- `tailored_resume_text` — readable resume text
- `tailored_pitch` — Stage 2 pitch (unchanged)

Also exposed on `GET /matches` and `GET /matches/{id}`.

## Storage (`applications`)

| Column | Meaning |
|--------|---------|
| `match_pitch` | Short Stage 2 pitch |
| `tailored_bullets` | JSON rewritten bullets |
| `tailored_resume_text` | Full tailored resume text |

## Local check

```powershell
# 1) List matches, copy an application id
curl http://localhost:8000/matches -H "Authorization: Bearer $TOKEN"

# 2) Tailor
curl -X POST http://localhost:8000/matches/<application_id>/tailor `
  -H "Authorization: Bearer $TOKEN"
```
