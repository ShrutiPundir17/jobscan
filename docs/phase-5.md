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

## WhatsApp in production (templates)

The Twilio **sandbox** works with free-form text, but a real WhatsApp sender
may only send free-form text within **24 hours of the user's last message to
you**. Everything else must be a **pre-approved template**, otherwise Twilio
accepts the message and WhatsApp drops it (error **63016**).

JobAgent sends three kinds of WhatsApp message. Give each one a template and
it is used automatically; leave a SID empty and that kind falls back to
free-form text (fine for the sandbox).

| Env var | Message | Variables |
|---------|---------|-----------|
| `TWILIO_TEMPLATE_MATCHES_SID` | New matches alert | `{{1}}` count, `{{2}}` top score, `{{3}}` dashboard link |
| `TWILIO_TEMPLATE_PASSWORD_RESET_SID` | Password reset link | `{{1}}` reset link |
| `TWILIO_TEMPLATE_LOGIN_EMAIL_SID` | "Forgot username" reply | `{{1}}` login email |

### Setup (one time)

1. **WhatsApp sender:** Twilio Console → Messaging → Senders → WhatsApp senders →
   register a number with your Meta Business account. Set
   `TWILIO_WHATSAPP_FROM=whatsapp:+<that number>`.
2. **Templates:** Twilio Console → Messaging → Content Template Builder → create
   three **Text** templates, category **Utility**, language English, using the
   texts below (variables are never at the very start or end — Meta rejects that).
3. Submit each for **WhatsApp approval** (usually minutes to a day).
4. Copy each approved template's **Content SID** (`HX…`) into the env vars above
   and redeploy.

**jobagent_matches** — Utility
```
JobAgent found {{1}} new job matches for you (top score {{2}}/100). Review them in your dashboard: {{3}} — you can turn these alerts off in Preferences.
```
Sample values: `3`, `87`, `https://jobscan-web.vercel.app`

**jobagent_password_reset** — Utility
```
Use this link to reset your JobAgent password (valid for 1 hour): {{1}} — if you did not request this, you can ignore this message.
```
Sample value: `https://jobscan-web.vercel.app/?reset_token=abc123`

**jobagent_login_email** — Utility
```
Your JobAgent login email is {{1}} — sign in with this email and your password.
```
Sample value: `you@example.com`

### Delivery statuses

Stored on each notification (`whatsapp_status`):
`sent` (accepted by Twilio — delivery itself is asynchronous), `skipped`
(not configured / disabled / no phone), `failed:needs_template` (63016),
`failed:invalid_number`, `failed:sandbox_not_joined`, `failed:sender_not_whatsapp`,
`failed:bad_credentials`, `failed:bad_template_variables`, `failed:template_not_found`,
`failed:rate_limited`, or `failed:twilio_<code>`.

Logs only ever show the last 4 digits of a phone number.

## Local check

```powershell
# After scoring with persist=true:
curl http://localhost:8000/notifications -H "Authorization: Bearer $TOKEN"
```
