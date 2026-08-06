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

## Still stubbed
- delete, set-primary
