# dokscanner-central

Config service for DOKScanner.exe installs: admins publish projects/templates
once; each registered PC pulls what it is assigned. Stores no scanned
documents. Spec: `docs/superpowers/specs/2026-08-29-central-admin-dashboard-design.md`.

## Environment

| Variable | Required | Meaning |
|---|---|---|
| `CENTRAL_DATABASE_URL` | yes | e.g. `postgresql+psycopg://user:pw@host/dokcentral` |
| `CENTRAL_SESSION_SECRET` | yes | 32+ random characters; rotating it logs every admin out |
| `CENTRAL_COOKIE_SECURE` | no | default `1`; set `0` only for local HTTP development |

## Run

    pip install -r central/requirements.txt
    python -m central.manage create-admin <username>      # first admin
    uvicorn central.app:create_app_from_env --factory --host 127.0.0.1 --port 8000

Run behind a reverse proxy that terminates TLS. Do not expose uvicorn directly.

## Deployment requirements

- HTTPS only. Keep `CENTRAL_COOKIE_SECURE=1` (the default) in production.
- The reverse proxy MUST cap request body size (for nginx:
  `client_max_body_size 1m;`). The service's 256 KB config cap applies only
  after the body has been parsed, so it does not protect against oversized
  uploads on its own.

## Enrolling a PC

1. Dashboard/API: `POST /admin/pcs {"display_name": "..."}` returns a one-time
   `enrollment_code` (valid 60 minutes).
2. On the PC, the installer/Admin panel calls `POST /pcs/register {"code": ...}`
   and stores the returned `api_key` in the DPAPI keystore.
3. Lost or compromised PC: `DELETE /admin/pcs/{id}/key`, then issue a new code.

## Admin sessions and lockout

- Sessions are server-side revocable: logout bumps the admin's
  `session_version`, invalidating every cookie issued before it. Sessions also
  expire after 8 hours.
- Lockout: 5 consecutive failed logins lock the account for 15 minutes. A
  locked account and a wrong password return the same 401, so lockout state
  cannot be probed.

## Security properties

- Stored project/template configs never contain credentials.
- PC API keys are stored hashed; the plaintext is shown once, at registration.
- Enrollment codes are single-use and expire after 60 minutes.

## Operational notes

- Tables are created with `create_all()`; adopt Alembic before the first
  schema change after go-live.
- `audit_events` grows by one row per PC poll (default every 10 minutes);
  plan retention before large fleets.
- A project pushed to "all PCs" reaches only PCs that exist at that moment.
