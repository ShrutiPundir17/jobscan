# JobAgent

AI-powered platform that automates the job search and application process.

Upload a resume once. JobAgent scans job portals around the clock, scores fit like a recruiter, rewrites your resume per role, applies for you, and tracks every application in one dashboard.

## Monorepo layout

```
JOBSCAN/
├── apps/
│   ├── web/              # Candidate dashboard (UI)
│   └── api/              # FastAPI backend + Celery app
├── packages/
│   └── shared/           # Shared types, schemas, utilities
├── workers/
│   ├── job-scanner/      # Periodic job portal scanning (logic later)
│   ├── matcher/          # Fit scoring / resume rewrite (logic later)
│   └── apply-agent/      # Auto-apply (logic later)
├── docs/
├── scripts/
├── docker-compose.yml    # Postgres, Redis, backend, Celery
└── .env.example
```

## Docker stack (Phase 1)

| Service | Role |
|---------|------|
| `postgres` | Primary database |
| `redis` | Celery broker + cache |
| `backend` | FastAPI on port 8000 |
| `celery-worker` | Background task runner |
| `celery-beat` | Scheduler (job-scan cadence later) |

```bash
cp .env.example .env
docker compose up --build
```

- API: http://localhost:8000
- Health: http://localhost:8000/health
- Web dashboard: http://localhost:5173

## Phases

| Phase | Focus |
|-------|--------|
| 1 | Setup & infrastructure |
| 2+ | Coming next |
