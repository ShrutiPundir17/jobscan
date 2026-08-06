# Phase 1 — Setup & Infrastructure

## Done
- Local Git repository + monorepo folder structure
- Docker Compose: Postgres, Redis, FastAPI backend, Celery worker + beat
- Database models + Alembic migration:
  - `users`
  - `resumes`
  - `jobs`
  - `applications`

## Run

```bash
cp .env.example .env
docker compose up --build -d
docker compose exec backend alembic upgrade head
```

## Next
Await next phase instructions.
