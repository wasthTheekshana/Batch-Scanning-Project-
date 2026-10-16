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

Ready-made files for a production install (Docker Compose with PostgreSQL and a TLS
proxy, or systemd + nginx, plus a nightly backup and a restore procedure) are in
`central/deploy/` - start with `central/deploy/DEPLOY.md`. The notes below are the
rules those files implement.

- HTTPS only. Keep `CENTRAL_COOKIE_SECURE=1` (the default) in production.
- The reverse proxy MUST cap request body size (for nginx:
  `client_max_body_size 1m;`). The service's 256 KB config cap applies only
  after the body has been parsed, so it does not protect against oversized
  uploads on its own.
- The deployment MUST include `core/profile_manager.py` and
  `core/project_schema.json`: `central` imports them to validate project
  configs. Shipping `central/` alone fails at import time.
- On an empty Postgres, start the FIRST time with ONE worker
  (`--workers 1`) so the `create_all()` DDL does not race between workers;
  scale workers afterwards.
- `create_all()` never alters existing tables. At the first production deploy
  generate an Alembic baseline from the models and `alembic stamp` it, so
  later schema changes are migrations.

## Enrolling a PC

1. Dashboard/API: `POST /admin/pcs {"display_name": "..."}` returns a one-time
   `enrollment_code` (valid 60 minutes).
2. On the PC, an administrator opens Admin > Central, enters the server URL
   (HTTPS) and the code, and clicks Register. The returned `api_key` is stored
   DPAPI-protected on that PC and never shown or logged. The PC then pulls its
   assigned projects at startup and on the server's poll interval.
   Updated or new projects are written locally at once and apply to the next
   scan batch (no restart; a batch already running keeps the settings it started with). The PC keeps its own scanner, export and
   delivery settings; central owns the processing settings (OCR, QC,
   separation, rules, index fields).
3. Lost or compromised PC: `DELETE /admin/pcs/{id}/key`, then issue a new code.

## Admin sessions and lockout

- Sessions are server-side revocable: logout bumps the admin's
  `session_version`, invalidating every cookie issued before it. Sessions also
  expire after 8 hours.
- Lockout: 5 consecutive failed logins lock the account for 15 minutes. A
  locked account and a wrong password return the same 401, so lockout state
  cannot be probed. Lockout does NOT revoke live sessions: an already-issued
  cookie stays valid until logout or the 8 h expiry.
- Authentication events (`admin_login`, `admin_login_failed`, `admin_locked`,
  `admin_logout`, `admin_created`) are written to `audit_events` and to the
  `dokscanner.central` logger (INFO for login/logout, WARNING for failed/locked;
  no passwords). Unknown usernames are recorded without the attempted name.

## Security properties

- Stored project/template configs never contain credentials.
- PC API keys are stored hashed; the plaintext is shown once, at registration.
- Enrollment codes are single-use and expire after 60 minutes.

## Operational notes

- Tables are created with `create_all()`; see "Deployment requirements" for
  the Alembic baseline.
- Not available yet (needed by dashboard plan 3): admin password reset or
  disable, "revoke all sessions", PC deletion, and an audit-log read API.
- Audit text can contain user-supplied characters: any UI that shows it MUST
  render it as plain text (never HTML).
- `audit_events` grows by one row per PC poll (default every 10 minutes);
  plan retention before large fleets.
- A project pushed to "all PCs" reaches only PCs that exist at that moment.


## Admin dashboard

Open `/dashboard` (the root `/` redirects there) and sign in with an admin
account. The dashboard is three static files in `central/dashboard/`
(`index.html`, `app.js`, `style.css`; ship the folder with the service). It has
no build step and loads nothing from other sites; every response carries a strict
Content-Security-Policy (`default-src 'none'`, scripts and styles from the
service only), `X-Frame-Options: DENY`, `nosniff`, and admin pages are never cached.

Pages: Overview (PC and admin counts, PCs that stopped checking in, recent
activity), PCs (register, enrollment code, rename, revoke key, delete),
Projects and Templates (versions, JSON view and edit, assign or unassign PCs),
Admins (create, disable/enable, unlock, reset password, sign out, sign
everyone out), Audit log (filter by action or user, paged) and Settings
(PC poll interval). Each admin changes their own password from the header.

Try it locally with sample data: `python scripts/central_demo.py` (sign in as
`admin` / `demo-admin-passphrase`).

### Admin API added for the dashboard

| Endpoint | Purpose |
|---|---|
| `GET/POST /admin/users` | list / create admins (password at least 12 characters) |
| `POST /admin/users/{id}/disable` `enable` `unlock` | a disabled admin cannot sign in and is signed out; you cannot disable yourself or the last enabled admin |
| `POST /admin/users/{id}/password` | reset another admin's password (signs them out) |
| `POST /admin/users/{id}/revoke-sessions`, `POST /admin/users/revoke-all-sessions` | force sign-out |
| `POST /admin/me/password` | change your own password (needs the current one) |
| `PATCH /admin/pcs/{id}`, `DELETE /admin/pcs/{id}` | rename / delete a PC (and its assignments and codes) |
| `GET /admin/audit` | audit log, newest first, `limit`, `before_id`, `action`, `actor` |
| `GET /admin/audit/actions`, `GET /admin/overview` | filter list, dashboard summary |
| `GET /admin/project-defaults` | a complete starting configuration for a new project |

A real-browser test of the dashboard is in `scripts/dashboard_e2e/`.
