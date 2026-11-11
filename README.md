# JobAgent

AI job-search assistant for the Indian market. Upload your resume once; JobAgent
scans job portals for you, scores every job against your resume, rewrites your
resume for a role, fills in employer application forms (you approve before
anything is sent), and tracks every application in one dashboard.

**Live:** [jobscan-web.vercel.app](https://jobscan-web.vercel.app)

## What it does

| Feature | How |
|---------|-----|
| **Resume parsing** | PDF/DOCX → structured profile (Gemini) → vector embedding (pgvector) |
| **Job scanning** | Naukri, LinkedIn, Internshala, Foundit, Unstop via Playwright; retries, per-portal cooldowns when a site blocks us, never loses partial work |
| **Matching** | Stage 1: vector similarity in Postgres. Stage 2: Gemini scores the shortlist 0–100 with reasons, skill gaps and a pitch |
| **Resume tailoring** | Rewrites your bullets for a specific job description without inventing experience |
| **Auto-apply** | Fills public Greenhouse / Lever / Ashby forms in a dry run; you review every value and approve; submission is confirmed or flagged "check if received" — never sent twice |
| **Jobs browser** | Search and filter everything the scanner found, with your status on each job |
| **Tracking** | Kanban of applications (review → applied → interview → offer) |
| **Notifications** | In-app, email (Resend / SMTP), WhatsApp (Twilio) |
| **Accounts** | Email + password, password reset, Google Sign-In |

## Architecture

```
 Browser ── Vercel (React + Vite) ──HTTPS──► FastAPI ──► Postgres + pgvector
                                               │  ▲          (users, jobs, embeddings,
                                               │  │           resume files)
                                               ▼  │
                                  Background tasks ──► Redis (locks, cooldowns, status)
                                  • notifications, embeddings, auto-apply (Chromium)
                                  • job scans (Chromium, scheduled)
                                               │
                                               ▼
                                     Gemini · Resend/SMTP · Twilio
```

Background tasks run either on a **Celery worker** (`TASK_MODE=worker`, the
default — Docker and paid hosts) or, on hosts without a worker such as Render's
free plan, **inside the API process one at a time** with scans in **GitHub
Actions** (`TASK_MODE=inline`).

## Repository layout

```
apps/
  api/                  FastAPI app, SQLAlchemy models, Alembic migrations, Celery tasks
    app/api/            HTTP routes (auth, users, resumes, jobs, matches, applications, …)
    app/services/       Matching, tailoring, auto-apply state machine, scan guard, …
    app/tasks/          Background tasks (+ dispatch for worker / inline mode)
    app/scan_once.py    One-shot scan entrypoint (GitHub Actions)
    tests/              API test suite (real Postgres + pgvector)
  web/                  React dashboard (Vite + TypeScript)
workers/
  job-scanner/          Playwright portal scrapers
  apply-agent/          ATS form detection, filling and submission (+ its tests)
deploy/                 Production Dockerfile and entrypoints
docs/                   Phase write-ups and deployment guides
.github/workflows/      Scheduled job scan workflow
render.yaml             Render blueprint for the free API deployment
docker-compose*.yml     Local stack and self-hosted production stack
```

## Run it locally

Needs Docker Desktop and a [Gemini API key](https://aistudio.google.com/app/apikey).

```bash
cp .env.example .env          # set GOOGLE_API_KEY
docker compose up --build
```

| URL | What |
|-----|------|
| http://localhost:5173 | Web dashboard |
| http://localhost:8000/docs | API (interactive docs) |
| http://localhost:8000/health | Health check (Postgres + Redis) |

Migrations run automatically. The stack includes Postgres (pgvector), Redis, the
API, a Celery worker and the scheduler.

## Tests

```bash
# API — needs a throwaway Postgres + pgvector (every test truncates all tables)
docker run -d --name jobagent-test-pg -p 55499:5432 \
  -e POSTGRES_USER=jobagent -e POSTGRES_PASSWORD=jobagent -e POSTGRES_DB=jobagent_test \
  pgvector/pgvector:pg16
cd apps/api
pip install -r requirements-dev.txt
python -m playwright install chromium
python -m pytest tests

# Auto-apply agent — real headless Chromium against local mock ATS forms
cd workers/apply-agent
pip install -r requirements.txt pytest
python -m pytest tests

# Web — type-check + production build
cd apps/web && npm ci && npm run build
```

External services (Gemini, email, WhatsApp, Redis) are mocked, so no secrets are needed.

## Deploy

- **Free:** Vercel + Render + Supabase + Upstash + GitHub Actions —
  step-by-step in [docs/deploy-free.md](docs/deploy-free.md).
- **Self-hosted VPS:** Docker Compose + Caddy (automatic HTTPS) —
  [docs/deploy.md](docs/deploy.md).

## Configuration

All settings are environment variables; [`.env.example`](.env.example) lists
every one with comments. The ones you must set in production:

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | Postgres with pgvector |
| `REDIS_URL` | Redis |
| `JWT_SECRET_KEY` | Signs login tokens — long and random |
| `GOOGLE_API_KEY` | Gemini (parsing, matching, tailoring, embeddings) |
| `CORS_ORIGINS` | Website origin(s) allowed to call the API |
| `TASK_MODE` | `worker` (default) or `inline` (no background worker) |

## Status and limits

- Auto-apply only works on public **Greenhouse, Lever and Ashby** forms. Portal
  jobs (Naukri, Internshala, Foundit, Unstop, LinkedIn Easy Apply) need a logged-in
  account and stay manual. Required questions the agent can't answer truthfully
  (notice period, work authorization, …) send the application to "needs manual".
- Captchas are respected: the scanner backs off and the agent hands the form to you.
- WhatsApp alerts use the Twilio sandbox until a WhatsApp Business sender and
  approved message templates are set up.

Development history and design notes: [docs/](docs/README.md).
