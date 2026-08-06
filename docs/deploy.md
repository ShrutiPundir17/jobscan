# Deploy JobAgent for real testers (VPS + Docker)

Localhost only works on your machine. For friends/testers you need a **public HTTPS URL**.

This guide uses a small VPS + Docker Compose + Caddy (same stack as local: Postgres/pgvector, Redis, API, Celery, static web).

## Architecture

```
https://YOUR_DOMAIN/        → static web (nginx)
https://YOUR_DOMAIN/api/*   → FastAPI (Caddy strips /api)
https://YOUR_DOMAIN/health  → API health
```

Browser calls same-origin `/api`, so CORS is simple.

## 1. Prerequisites

- VPS (2 GB RAM minimum; 4 GB safer for Playwright scanner), Ubuntu 22.04+
- Domain DNS **A record** → VPS public IP (required for Let’s Encrypt)
- Docker Engine + Compose plugin
- Secrets: Gemini API key; SMTP for email alerts (WhatsApp sandbox optional)

## 2. Server setup

```bash
# On the VPS
sudo apt update && sudo apt install -y git
# Install Docker: https://docs.docker.com/engine/install/
git clone <YOUR_REPO_URL> JOBSCAN
cd JOBSCAN
cp .env.example .env
```

## 3. Production `.env` (required)

Edit `.env` — at minimum:

```env
# Public hostname (no https://)
DOMAIN=jobagent.yourdomain.com

APP_ENV=production
DEBUG=false

# Strong secrets — do not reuse local defaults
POSTGRES_PASSWORD=<long-random>
JWT_SECRET_KEY=<long-random>

# Same-origin API (baked into the web build; relative /api is fine)
VITE_API_URL=/api
CORS_ORIGINS=https://jobagent.yourdomain.com
APP_PUBLIC_URL=https://jobagent.yourdomain.com

GOOGLE_API_KEY=<your-gemini-key>

# Email alerts for testers (recommended)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=...
SMTP_PASSWORD=...
SMTP_FROM_EMAIL=...
SMTP_USE_TLS=true
```

Optional: Twilio WhatsApp (sandbox still needs users to join; email is more reliable for beta).

## 4. Start production stack

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
curl -sS https://YOUR_DOMAIN/health
```

Migrations run automatically on API start (`alembic upgrade head`).

Only ports **80/443** are published (via Caddy). Postgres and Redis stay internal.

## 5. Smoke test before inviting people

1. Open `https://YOUR_DOMAIN`
2. Register → set preferences → upload + parse resume
3. **Matches** → Find matches → Tailor → Apply
4. **Applications** → change status / notes
5. Profile → Send test alert (email)

Then share the HTTPS URL with testers (closed beta).

## 6. Useful commands

```bash
# Logs
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f backend celery-worker caddy

# Rebuild after code pull
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

# Stop
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
```

## Cost / ops notes

- Gemini + SMTP usage scales with tester activity
- Job scanner uses Chromium inside `celery-worker` — keep concurrency low
- WhatsApp Business (templates) is a separate Twilio upgrade; not required for UI beta
- Auto-apply is not deployed — Apply marks tracking and opens the employer JD

## Fallback: laptop tunnel (demo only)

PC must stay awake; URL dies when you sleep or close the tunnel.

```bash
# Terminal 1 — local stack
docker compose up -d

# Terminal 2 — public HTTPS to the web UI
# Install cloudflared or ngrok, then e.g.:
cloudflared tunnel --url http://localhost:5173
```

If the tunnel URL is only for the web, set `VITE_API_URL` to a **second tunnel** aimed at `:8000`, rebuild/restart web, and add that web origin to `CORS_ORIGINS`. Prefer the VPS path for real testers.

## Files added for deploy

| File | Role |
|------|------|
| [`docker-compose.prod.yml`](../docker-compose.prod.yml) | Prod overlay (no public DB ports, no bind mounts, Caddy) |
| [`Caddyfile`](../Caddyfile) | HTTPS + `/api` reverse proxy |
| [`deploy/Dockerfile.api`](../deploy/Dockerfile.api) | API image including `workers/job-scanner` |
| [`apps/web/Dockerfile`](../apps/web/Dockerfile) | Multi-stage; `production` target bakes `VITE_API_URL` |
