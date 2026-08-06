# Phase 5 — Match notifications (Email + WhatsApp)

When Stage 2 persists strong matches, JobAgent notifies the user.

## Architecture (enterprise)

Two layers — do **not** ask end users for SMTP/Twilio secrets.

| Layer | Who configures | What |
|-------|----------------|------|
| **Platform** | Ops / you (env / secrets manager) | `SMTP_*`, `TWILIO_*` — JobAgent sends on behalf of the product |
| **User** | Each candidate in the UI | Email toggle, WhatsApp toggle, phone number |

Users only opt in and add a phone. JobAgent’s backend uses the shared provider credentials to deliver.

## Goals

- In-app notification row per match batch
- Email via SMTP (when configured)
- WhatsApp via Twilio (when configured)
- User prefs: phone, email on/off, WhatsApp on/off

## Flow

```
POST /matches/score (persist=true)
  → applications upserted
  → Celery: notify_matches_found(user_id, application_ids)
      → notifications row (type=matches_found)
      → SMTP email (or skipped)
      → Twilio WhatsApp (or skipped)
```

## User prefs

`PATCH /users/me`:

```json
{
  "phone": "+9198XXXXXXXX",
  "notify_email_enabled": true,
  "notify_whatsapp_enabled": true
}
```

- Email defaults **on**
- WhatsApp defaults **off** (needs phone + Twilio)

## APIs

| Method | Path | Notes |
|--------|------|--------|
| GET | `/notifications` | List (supports `unread_only`) |
| PATCH | `/notifications/{id}/read` | Mark read |

## Env

```env
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM_EMAIL=
SMTP_FROM_NAME=JobAgent
SMTP_USE_TLS=true

TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886

APP_PUBLIC_URL=http://localhost:5173
```

If SMTP / Twilio are blank, delivery status is `skipped` but the **in-app** notification is still created.

## Local check

```powershell
# After scoring with persist=true:
curl http://localhost:8000/notifications -H "Authorization: Bearer $TOKEN"
```
