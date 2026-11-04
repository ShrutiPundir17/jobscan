# Phase 2 — User Module & Resume Parsing

## Done so far
- Resume upload (PDF/DOCX) with local text extraction
- `GET /resumes` and `GET /resumes/{id}`
- Gemini parsing via `POST /resumes/{id}/parse` → structured JSON in `parsed_data`
- Vector embeddings (pgvector + `gemini-embedding-001`, 768-dim)
- User preferences: `GET/PATCH /users/me`
  - target roles, preferred locations, auto-apply toggle, min match score
- Profile management dashboard (`apps/web` on http://localhost:5173)
  - login, preferences form, resume upload/parse/embed

## Resume management
- `POST /resumes/{id}/set-primary` — idempotent; 409 until the resume is parsed + embedded
  (the primary resume is what matching and auto-apply use)
- `DELETE /resumes/{id}` — removes the row and its file; returns `new_primary_id` when the
  primary was deleted (embedded resumes are promoted before newer unparsed ones) and
  `retired_auto_apply_plans` (plans waiting to use it must be prepared again); 409 while an
  auto-apply submission using it is in flight
- One primary per user is enforced by a partial unique index (migration `0011`); uploads,
  deletes and primary switches take a per-user row lock
- Resume page "Upload new" makes the new resume primary once it is parsed
