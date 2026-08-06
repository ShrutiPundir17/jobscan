# Workers

Background jobs that keep JobAgent running 24/7.

- `job-scanner/` — scans major portals on a schedule (e.g. every 2 hours)
  - See [job-scanner/README.md](./job-scanner/README.md) and [docs/phase-3.md](../docs/phase-3.md)
- `matcher/` — scores fit 0–100, explains reasoning, skill gaps, resume rewrite
- `apply-agent/` — fills forms and submits applications when enabled
