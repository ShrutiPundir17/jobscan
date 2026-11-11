# Workers

Browser automation packages used by the API's background tasks
(Celery worker, inline tasks, or the GitHub Actions scan).

- `job-scanner/` — Playwright scrapers for Naukri, LinkedIn, Internshala, Foundit, Unstop
  - See [job-scanner/README.md](./job-scanner/README.md) and [docs/phase-3.md](../docs/phase-3.md)
- `apply-agent/` — fills public Greenhouse/Lever/Ashby forms; submits only after the user approves
  - See [apply-agent/README.md](./apply-agent/README.md) and [docs/phase-8.md](../docs/phase-8.md)
