# Free deployment: Vercel + Render + Supabase + Upstash + GitHub Actions

| Piece | Service | What runs there |
|-------|---------|-----------------|
| Website | **Vercel** (already set up) | React app at `https://jobscan-web.vercel.app` |
| API | **Render** free web service | FastAPI (`deploy/Dockerfile.api`) with `TASK_MODE=inline` |
| Database | **Supabase** free | Postgres + pgvector (users, jobs, embeddings, resume files) |
| Redis | **Upstash** free | Scan locks, portal cooldowns, scan status |
| Job scans | **GitHub Actions** | `.github/workflows/scan.yml` every 3 hours |

Render's free plan has no background worker, so with `TASK_MODE=inline` the API
runs notifications, embeddings and auto-apply one at a time in a background
thread, and job scans (Chromium, minutes long) run in GitHub Actions instead.
Resume files are stored in the database, so Render's temporary disk is fine.

Do the steps in order — each one needs a value from the step before.

## 1. Supabase — database (≈5 min)

1. Sign up at **supabase.com** → **New project**.
   - Region: **Mumbai (ap-south-1)** (or the one nearest your users).
   - Database password: use **letters and numbers only** (symbols break the
     connection URL). Save it — you need it below.
2. **Database → Extensions** → search **vector** → enable it.
3. Top bar **Connect** → **Connection string** → choose **Session pooler**.
   Copy the URI, e.g.
   `postgresql://postgres.abcdefgh:[YOUR-PASSWORD]@aws-0-ap-south-1.pooler.supabase.com:5432/postgres`
   and replace `[YOUR-PASSWORD]` with your password.

   > Use the **Session pooler**, not "Direct connection": Supabase's direct
   > connection is IPv6-only on the free plan and Render cannot reach it.

This is your **`DATABASE_URL`**.

## 2. Upstash — Redis (≈3 min)

1. Sign up at **upstash.com** → **Create database** (Redis).
   - Type: Regional, region close to your Render region, free plan.
2. On the database page copy the URL that starts with **`rediss://`**
   (two s's = TLS), e.g. `rediss://default:AbCd…@apn1-xyz.upstash.io:6379`.

This is your **`REDIS_URL`**.

## 3. Render — API (≈15 min, mostly the first build)

1. Sign up at **render.com** with GitHub → **New → Blueprint**.
2. Pick the **jobscan** repo. Render reads `render.yaml` and shows the
   `jobagent-api` service (free plan).
3. Fill the secret values it asks for:
   - `DATABASE_URL` — from step 1
   - `REDIS_URL` — from step 2
   - `GOOGLE_API_KEY` — your Gemini key
   - `GOOGLE_OAUTH_CLIENT_ID` — only if you use "Continue with Google"
   - `GITHUB_DISPATCH_TOKEN` — optional, see step 6 (leave blank for now)
4. **Apply**. The first build takes ~10 minutes. Database tables are created
   automatically on start.
5. Open `https://<your-service>.onrender.com/health` — you should see
   `{"app":"ok","postgres":"ok","redis":"ok"}`.

Copy the service URL (e.g. `https://jobagent-api.onrender.com`).

## 4. Vercel — point the website at the new API (≈2 min)

1. Vercel → **jobscan-web** project → **Settings → Environment Variables**.
2. Set `VITE_API_URL` = your Render URL from step 3 (no trailing slash).
3. **Deployments → ⋯ → Redeploy** (the URL is baked in at build time).

Login and sign-up work again at this point.

## 5. GitHub — scheduled job scans (≈3 min)

1. GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**.
   Add: `DATABASE_URL`, `REDIS_URL`, `GOOGLE_API_KEY` (same values as Render).
   Optional: `SCANNER_PROXY_URL`.
2. **Actions** tab → **Job scan** → **Run workflow** to test it now.
   It then runs by itself every 3 hours.

The scan searches for the roles and cities your users set in Preferences
(most common first, up to 3 × 3), and embeds the new jobs for matching.

## 6. Optional — make "Scan jobs" start a scan immediately

Without this, the button tells users scans run automatically.

1. GitHub → your avatar → **Settings → Developer settings → Fine-grained tokens → Generate**.
2. Repository access: **Only select repositories → jobscan**.
   Permissions: **Actions → Read and write**. Nothing else.
3. Paste the token into Render as `GITHUB_DISPATCH_TOKEN` → the service redeploys.

## Good to know

- **Cold starts:** Render free sleeps after 15 minutes without traffic; the
  first request after that takes up to a minute.
- **Supabase pausing:** a free project pauses after a week with no activity.
  The 3-hourly scan keeps it active; if it ever pauses, press **Restore** in Supabase.
- **Limits change:** free-tier limits are set by each provider — check their
  pricing pages if something stops.
- **GitHub Actions terms** describe Actions as being for building and testing
  software. A light 3-hourly scan is common, but it is a grey area for a
  production workload; if GitHub ever objects, move the scan to any machine
  that can run `python -m app.scan_once`.
- **Old Railway data:** if your Railway database still exists, export it
  (`pg_dump`) and import it into Supabase before users sign up again.

## Local development

Unchanged: `docker compose up --build` runs everything with a real Celery
worker (`TASK_MODE=worker`, the default).
