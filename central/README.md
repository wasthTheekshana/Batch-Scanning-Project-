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
| `CENTRAL_DATA_KEY` | for the alert webhook and index data | the key that encrypts what the server stores for you; `python -m central.manage generate-key` makes one. Keep a copy apart from the database backups (see `deploy/DEPLOY.md`) |

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
- The schema is managed by Alembic migrations (`central/migrations/`). Starting the service brings the
  database to the latest one: an empty database gets every migration, a database made by the earlier
  `create_all()` is recognised as the baseline (stamped, nothing changed) and upgraded, and one already
  under migration gets the ones it lacks. On an empty Postgres start the first time with ONE worker
  (`--workers 1`) so two workers do not migrate at once; scale afterwards.
- To change the schema edit `central/models.py`, run `python -m central.manage new-migration "what changed"`,
  review and commit the file written in `central/migrations/versions/`.
  `python -m central.manage db-status` reports the version and any drift from the models (the test
  `tests/central/test_migrations.py` fails on drift); `python -m central.manage migrate` migrates without
  starting the service.

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

- Tables are created and changed by migrations; see "Deployment requirements".
- Built for the dashboard (see the table below and `docs/CENTRAL_API.md`): admin password reset,
  disable/enable/unlock, "revoke all sessions", PC deletion and the audit-log read API.
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

Pages: Overview (how the fleet is doing in a sentence and a status bar, the
stations that need a look and why, recent activity), **Fleet** (every station:
online or not, program version, whether it runs the current settings of each
assigned project, today's counts; stations needing attention first) and
**Activity** (documents, pages, batches and errors per day and per station, for
7 to 90 days), PCs (register, enrollment code, rename, revoke key, delete),
Projects and Templates (versions, JSON view and edit, assign or unassign PCs),
Admins (create, disable/enable, unlock, reset password, sign out, sign
everyone out), Audit log (filter by action or user, paged) and Settings
(PC poll interval). Each admin changes their own password from the header.

Try it locally with sample data: `python scripts/central_demo.py` (sign in as
`admin` / `demo-admin-passphrase`).

### What a PC reports (fleet monitoring)

With each sync a PC sends `POST /pcs/{id}/heartbeat` with: its **program version**, the **settings version
of each centrally managed project** it runs, and its **running totals for today** (UTC): documents, pages,
batches and errors (a document that was rejected, or whose delivery was reported unsuccessful). Nothing
else: no document text, index values, names, file names or paths. The server validates every field
(a version pattern, at most 200 projects, counts up to 10,000,000, a day within two days of now), keeps one
row per PC and day (a later report replaces the earlier one, it does not add to it), and deletes daily rows
older than 400 days. Heartbeats are not written to the audit log (one row per poll would bury it).

A PC that never reports (an older program) is shown as "not reported", never as behind. Rollout is
**current** when the PC runs the version assigned here, **behind** when it runs an older one or none,
**unknown** when it has not reported, and the PC is not counted as behind while its access is revoked.
A PC is **online** if it checked in within three polling intervals (at least five minutes).

### Alerts

`GET /admin/alerts` (rules in `central/alerts.py`) lists what needs an administrator's attention. A station
still inside the grace window (three polling intervals, at least five minutes) raises nothing: one missed poll
is not an incident.

| Kind | Raised when | Level |
|---|---|---|
| `offline` | the station is silent beyond the grace window | warning; critical after 24 hours |
| `behind` | a settings version was published longer ago than the grace window and the station has not applied it (one alert per project; not raised for an offline station, and never for one that does not report) | warning; critical after 24 hours |
| `errors` | the station reports errors today | note for 1 to 4; warning from 5 |
| `revoked` | the station's access key was revoked | note |
| `not_enrolled` | added more than 7 days ago and never connected | note |

Each alert has a stable `id` (for example `behind:<pc>:<project>`), the station, a plain message, and `since`
(when the condition began, when known). The dashboard's Overview lists them and the side menu shows how many
are critical or warnings. Delivery by e-mail or webhook is not built: the alerts are read in the dashboard.

### Alert webhook

An administrator can give the server one webhook address (dashboard: Settings, Alert notifications). A background worker
(`central/notify.py`) then sends a message when an alert **opens**, becomes **critical** (`alert.escalated`), is still
critical 12 hours after the last message (`alert.reminder`), or is **resolved**. The chosen minimum level (notes,
warnings, or critical only) decides which alerts are announced. Each change is sent once, even with several server
processes (each is claimed in the database first), and a failed send is retried on the next pass.

    POST <your address>
    Content-Type: application/json
    X-DOK-Event: alert.opened          (alert.escalated, alert.reminder, alert.resolved, test)
    X-DOK-Timestamp: 1760860000        (seconds since 1970)
    X-DOK-Signature: sha256=<hex>      (only when a signing secret is set)

    {"event": "alert.opened", "at": "2026-10-18T09:00:00Z",
     "text": "[Critical] Galle: Stopped checking in",
     "alert": {"id": "offline:<pc>", "kind": "offline", "severity": "critical", "pc_id": "...",
               "pc_name": "Galle", "message": "Stopped checking in", "since": "2026-10-16T08:00:00Z"}}

`text` is ready to read (Slack, Teams and similar services show it as it is). To check a message came from the server,
compute `HMAC-SHA256(secret, timestamp + "." + raw body)`, compare it with the signature (constant-time), and reject a
timestamp more than a few minutes old.

Safety: the address must be https (plain http only with `CENTRAL_WEBHOOK_ALLOW_HTTP=1`, development); it is stored
encrypted and never returned (the API shows only its host name); an address that resolves to the server itself, to a
link-local address (cloud metadata) or to a multicast address is refused, and `CENTRAL_WEBHOOK_BLOCK_PRIVATE=1` refuses
private networks too; redirects are not followed, the answer is ignored, the timeout is 10 seconds, and any answer
other than 2xx counts as a failure. The audit log records that a message was sent or failed and the host, never the
address or secret. Endpoints: `GET/PUT /admin/webhook`, `POST /admin/webhook/test` (an auditor can read the status, not change it).

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
| `GET /admin/fleet` | every PC with its status, program version, settings rollout and today's counts, and a summary (read-only: an auditor may call it) |
| `GET /admin/alerts` | what needs attention now, most serious first, with counts per level (read-only; computed on request, so an alert exists exactly while its condition does) |
| `GET /admin/stats?days=14` | per-day totals (1 to 90 days, quiet days included as zeros) and each PC's total, busiest first |
| `GET /admin/project-defaults` | a complete starting configuration for a new project |

A real-browser test of the dashboard is in `scripts/dashboard_e2e/`.
