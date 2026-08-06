# Phase 4 — AI Matching Engine

Two-stage pipeline: cheap vector pre-filter, then Gemini deep-score on the shortlist.

## Goals

- Stage 1: milliseconds cosine search over pgvector (no LLM)
- Stage 2: Gemini deep score with reasons, gaps, and a tailored pitch
- Persist strong matches onto `applications` for the user

## Steps

| # | Step | Status |
|---|------|--------|
| 1 | Stage 1 — embedding similarity search | **Done** |
| 2 | Stage 2 — LLM deep-score (reasons, gaps, pitch) | **Done** |
| 3 | Persist matches + list/get API | **Done** |

## Stage 1 — Embedding similarity search

### Prerequisites (already from Phase 2–3)

- Resume embeddings (`POST /resumes/{id}/embed`) — `gemini-embedding-001`, 768-dim
- Job rows in Postgres + HNSW cosine indexes (`ix_jobs_embedding_cosine`)

### What we added

1. **Job embedding pipeline**
   - Celery task `app.tasks.job_embeddings.embed_pending_jobs`
   - Queued after every `scan_jobs` run
   - Beat: every 30 minutes (backfill)
   - Manual: `POST /scanner/embed-jobs`
   - Ingest clears embedding when title / description / company change

2. **Similarity search**
   - Service: `app/services/match_search.py`
   - pgvector cosine distance → similarity `1 - distance` → score `0–100`
   - Soft filter on `preferred_locations` (keeps remote / India-wide)
   - Defaults: top 50, min similarity `0.35` (`MATCH_*` env)

3. **API**
   - `POST /matches/search` — Stage 1 candidates (auth required)

```http
POST /matches/search
Authorization: Bearer <token>
Content-Type: application/json

{
  "resume_id": null,
  "limit": 30,
  "min_similarity": 0.35,
  "apply_location_prefs": true
}
```

Uses primary resume when `resume_id` is omitted. Resume must already be embedded.

### Local check

```powershell
# 1) Ensure jobs have vectors
curl -X POST http://localhost:8000/scanner/embed-jobs -H "Authorization: Bearer $TOKEN"

# 2) Vector search
curl -X POST http://localhost:8000/matches/search `
  -H "Authorization: Bearer $TOKEN" `
  -H "Content-Type: application/json" `
  -d "{}"
```

Or via Celery:

```powershell
docker compose exec celery-worker celery -A app.celery_app.celery call app.tasks.job_embeddings.embed_pending_jobs
```

## Stage 2 — Gemini deep-score

### What we added

1. **Service** `app/services/match_score.py`
   - Runs Stage 1 shortlist (default top 10 via `MATCH_DEEP_SCORE_LIMIT`)
   - Gemini JSON score: `score`, `reasoning`, `skill_gaps`, `tailored_pitch`
   - Uses `GEMINI_MODEL` (default `gemini-flash-latest`)

2. **Persistence**
   - When `persist=true` and LLM score ≥ user's `min_match_score` (default 70):
     upsert `applications` with status `pending_review`
   - Does not regress status for applications already applying / applied / later stages

3. **API**
   - `POST /matches/score` — Stage 2 deep-score (auth required)

```http
POST /matches/score
Authorization: Bearer <token>
Content-Type: application/json

{
  "resume_id": null,
  "limit": 5,
  "min_similarity": 0.35,
  "apply_location_prefs": true,
  "persist": true,
  "min_match_score": 70
}
```

### Local check

```powershell
curl -X POST http://localhost:8000/matches/score `
  -H "Authorization: Bearer $TOKEN" `
  -H "Content-Type: application/json" `
  -d '{"limit": 5, "persist": true}'
```

## Stage 3 — Persisted matches per user

Each strong Stage 2 result is saved on `applications` for that user:

| Column | Meaning |
|--------|---------|
| `match_score` | LLM score 0–100 |
| `match_verdict` | `strong` (≥85) / `good` (≥70) / `partial` (≥50) / `weak` |
| `skill_gaps` | JSON list of gaps |
| `match_reasoning` | Why this score |
| `tailored_resume_text` | Tailored pitch |
| `status` | `pending_review` when first persisted |

### API

- `GET /matches` — list persisted matches (score, verdict, gaps)
- `GET /matches/{application_id}` — one match detail

```http
GET /matches?min_score=70&status=pending_review&limit=20
Authorization: Bearer <token>
```

### Local check

```powershell
curl http://localhost:8000/matches?min_score=70 `
  -H "Authorization: Bearer $TOKEN"
```

## Next

- Dashboard UI to review / approve / apply from persisted matches
- See [Phase 5 — Match notifications](./phase-5.md) for email + WhatsApp alerts
