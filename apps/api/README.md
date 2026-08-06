# JobAgent API

FastAPI backend + Celery (Redis broker) + Postgres.

## Local with Docker

From the repo root:

```bash
cp .env.example .env
docker compose up --build
```

Services:
- API: http://localhost:8000
- Health: http://localhost:8000/health
- Postgres: localhost:5432
- Redis: localhost:6379
- Celery worker + beat

## Database

Models: `users`, `resumes`, `jobs`, `applications` (see `app/models.py`).

Apply migrations:

```bash
docker compose exec backend alembic upgrade head
```

## Auth

| Method | Path | Auth |
|--------|------|------|
| POST | `/auth/register` | no |
| POST | `/auth/login` | no |
| GET | `/auth/me` | Bearer JWT |

## Route skeleton

Stub endpoints return **501** until implemented. Auth works for real.

| Tag | Paths |
|-----|-------|
| users | `GET/PATCH /users/me` |
| resumes | **upload, list, get** (live); delete, set-primary, parse still stubbed |
| jobs | list, get |
| matches | **`POST /matches/search`**, **`POST /matches/score`**, **`GET /matches`**, **`GET /matches/{id}`**, **`POST /matches/{id}/tailor`** |
| applications | list, get, apply, update, withdraw |
| notifications | **`GET /notifications`**, **`PATCH /notifications/{id}/read`** |
| scanner | trigger, **embed-jobs** |

Interactive docs: http://localhost:8000/docs

