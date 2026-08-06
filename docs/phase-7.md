# Phase 7 — Casual-user UI, apply tracking, deploy notes

## Shipped in this phase

### Matches dashboard (web)
- **Matches** tab: list persisted scores, verdicts, skill gaps
- **Find matches** → `POST /matches/score` (persist)
- **Scan jobs** → queue portal scrape + job embeddings
- **Tailor resume** → `POST /matches/{id}/tailor`
- **Apply** → marks application `applied` and opens the employer JD (manual form)

### Applications dashboard (web)
- **Applications** tab: filter by status, update status/notes, withdraw
- Backend unstubbed: `GET/PATCH /applications`, `POST .../apply`, `POST .../withdraw`

### Lightweight apply (not auto-apply)
`POST /applications/{id}/apply` sets status + `applied_at` and returns `job_url`.  
The user finishes the employer form with the tailored resume. **Browser auto-apply agent is not built.**

---

## Still out of scope / production follow-ups

### Public URL (not local-only)
**Canonical guide:** [deploy.md](./deploy.md) — VPS + `docker-compose.prod.yml` + Caddy HTTPS.

Quick demo tunnel (laptop must stay on) is documented there as a fallback only.

### WhatsApp beyond sandbox
Sandbox needs the user to join and stay inside the **24h session window** (Twilio error **63016** when outside).

For production:
1. Twilio WhatsApp Business / approved sender
2. Message **templates** for outbound outside the session window
3. Keep `notify_whatsapp_enabled` + verified `phone` on the user profile

Email alerts already work with SMTP once configured.

### Auto-apply
Not implemented. Preferences may show an auto-apply toggle; it does not submit applications for you. Next step would be a guarded agent (site-specific adapters, rate limits, human confirm).

---

## Smoke checklist
1. Log in → Profile / Matches / Applications tabs
2. Matches → Find matches → see score + gaps
3. Tailor → copy text → Apply (JD opens, status becomes `applied`)
4. Applications → change status / notes / withdraw
5. Notifications test (email + WhatsApp sandbox if joined)
