# apply-agent

Playwright agent that fills public ATS application forms (Greenhouse, Lever, Ashby).

- `detect.py` — find and canonicalize an ATS form URL from a job URL, payload, JD or job page HTML
- `forms.py` — inspect a form, map fields to the applicant profile, list unanswerable required questions
- `agent.py` — `ApplyAgent.prepare()` fills without submitting; `ApplyAgent.submit()` re-fills,
  checks values match the approved plan, submits, and requires a confirmation page

Used by the API's Celery tasks (`app/tasks/auto_apply.py`). Flow, safety rails and env:
[docs/phase-8.md](../../docs/phase-8.md).

```powershell
cd workers/apply-agent
python -m pytest tests   # needs: pip install playwright pytest; playwright install chromium
```
