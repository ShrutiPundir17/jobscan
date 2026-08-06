# JobAgent Web

Profile management dashboard (login, preferences, resumes).

## Local

```bash
cd apps/web
npm install
npm run dev
```

Open http://localhost:5173

API default: `http://localhost:8000` (`VITE_API_URL` to override).

## Docker

From repo root (dev / HMR):

```bash
docker compose up --build web
```

Production static build bakes `VITE_API_URL` (default `/api` behind Caddy). See [docs/deploy.md](../../docs/deploy.md).
