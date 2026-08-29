# Central Admin Dashboard & Multi-PC Config Sync — Design Spec

Status: approved by user 2026-08-29, ready for implementation planning.
Corresponds to Enhancement Plan §3.3 (Multi-Site Consolidation & Central
Reporting Layer) and §3.7 (API/Integration Capability), both previously
deferred (see `docs/ENHANCEMENT_PLAN_STATUS.md`). This spec un-defers them
by making the underlying architectural decision explicit: DOKScanner
becomes a fleet of standalone SQLite-backed installs that additionally
sync to one central server, rather than staying fully standalone.

## Problem

Today each DOKScanner.exe install is fully independent: its own local
SQLite DB (`dokscanner.db`), its own templates/project configs, its own
RBAC. When installed on more than one PC, there is no way for an admin to
create or edit a project/template once and have every PC pick it up —
the admin must configure each PC separately. There is also no way to see,
from one place, what any of the fleet's PCs are doing or how they're
performing in aggregate.

## Goals (this spec = Phase 1 only; Phases 2-4 are outlined for context)

- An admin manages projects and templates from one central web dashboard.
- Every registered PC automatically picks up the current project/template
  config without an admin touching that PC directly.
- Scanning on any PC is never blocked by the central server being
  unreachable — the PC keeps working off its last-known-good local config.
- No change to the existing single-PC behavior when a PC is not
  registered against a central server (fully backward compatible).

## Non-goals (this spec)

- Real-time fleet status / live job monitoring (Phase 2).
- Aggregated cross-PC QC/rejection/audit reporting (Phase 3).
- Centralized operator user/role management (Phase 4).
- Multi-site / intermittent-connectivity / offline queuing support — out
  of scope entirely; this design assumes PCs and the central server share
  reliable connectivity (confirmed: same LAN or reliable internet path,
  not disconnected field sites).
- Migrating each PC's local SQLite DB to anything else. SQLite stays
  local and authoritative for that PC's own operational data.

## Architecture

A new, separately deployed service, `dokscanner-central`, independent of
the existing DOKScanner.exe codebase/repo boundary (may live in the same
git repo under a new top-level directory, e.g. `central/`, or a separate
repo — implementation plan should confirm with the user before coding).
It consists of:

- **Backend API** — Python, FastAPI.
- **Database** — Postgres. Stores only central-scope data (registered
  PCs, projects, templates, config version history, admin users). It
  never stores scanned documents/images or per-PC operational data
  (batches, jobs, custody events) — those remain local to each PC's
  SQLite DB in every phase, including future ones, per the non-goal above
  unless a later phase's spec explicitly revisits this.
- **Admin dashboard** — React SPA, served as a static build (fronted by
  the FastAPI app or a simple static file server — implementation plan
  decides).
- **Hosting** — cloud VPS (per user decision 2026-08-29).

Existing DOKScanner.exe gains one new component:

- **Sync client module** (new, e.g. `core/central_sync.py`) — talks to
  the central API over HTTPS. Inactive/no-op unless the PC has been
  registered against a central server (backward compatible with
  standalone installs).

```
 ┌─────────────────────┐        HTTPS/JSON         ┌──────────────────────────┐
 │ DOKScanner.exe (PC)  │ ───────────────────────▶  │ dokscanner-central       │
 │  - local SQLite DB   │  GET  /pcs/{id}/config     │  - FastAPI               │
 │  - template_manager  │ ◀───────────────────────  │  - Postgres              │
 │  - central_sync.py   │        (poll every N min,  │  - React admin dashboard │
 │    (new)             │         + on startup)      │                          │
 └─────────────────────┘                             └──────────────────────────┘
       (same pattern repeats for every registered PC)
```

## Data model (Postgres, Phase 1 scope)

- `pcs`: id, display_name, api_key_hash, registered_at, last_seen_at.
- `admin_users`: id, username, password_hash, role.
- `projects`: id, name, config_json, current_version_id.
- `project_templates`: id, name, category, config_json, built_in (mirrors
  the shape of the existing local `project_templates` table so config
  blobs can be pushed/pulled without reshaping).
- `config_versions`: id, project_id OR template_id, config_json,
  created_by (admin_user id), created_at — append-only, enables rollback
  and gives the admin a history view.
- `pc_assignments`: pc_id, project_id — which project(s) a given PC is
  currently configured to use (a PC may run more than one project).

## API (Phase 1 scope)

- `POST /pcs/register` — called once per PC at setup time (e.g. during
  first run or via the installer/admin panel); returns an API key. The
  PC stores this key via the existing DPAPI keystore (`data/keystore.py`).
- `GET /pcs/{id}/config` — the sync client polls this; returns the
  current assigned project/template config and its version number.
  Requires the PC's API key. Also accepts an `X-Operator-Id` header
  (whichever operator is logged into the app locally) purely for
  server-side audit logging — the operator does not separately
  authenticate to the server in Phase 1.
- `POST /pcs/{id}/heartbeat` — stub only in Phase 1 (accepts a call,
  updates `last_seen_at`); the richer status payload is Phase 2's
  concern.
- `PUT /admin/projects/{id}`, `PUT /admin/templates/{id}`,
  `POST /admin/projects/{id}/push` (assign/push to one or all PCs) —
  dashboard-facing, require admin login (session or JWT — implementation
  plan decides).

## Sync behavior

- On DOKScanner.exe startup, and every N minutes (default e.g. 10,
  configurable) while running, the sync client calls
  `GET /pcs/{id}/config`.
- If the returned config version is newer than the version stored
  locally, the sync client writes it into the PC's local SQLite via the
  existing `template_manager` / project-config machinery (no new local
  schema needed — it reuses `build_project_config`/`save_as_template`-
  style code paths) and logs the update to the local audit log.
- If the server is unreachable or returns an error, the sync client logs
  a warning and the PC continues operating on its last-known-good local
  config. Scanning/QC/indexing is never blocked by central-server
  availability.
- A PC that has never been registered (no API key stored) skips sync
  entirely and behaves exactly as it does today — this is the backward-
  compatibility guarantee.

## Security notes

- API key is the machine-identity credential; stored via DPAPI, never in
  plaintext config.
- All traffic over HTTPS.
- The central server's own auth (admin login), secrets management, and
  hardening are implementation-plan-level concerns but must follow the
  same bar as `docs/security/SDLC_POLICY.md` already sets for this
  project.
- Central DB stores no scanned document content and no per-document PII
  beyond what's already in a project/template config (which itself
  excludes live project identity/delivery destinations, per
  `core/template_manager.py`'s existing `_LIVE_ONLY_TOP_KEYS` split).

## Testing

- Backend: pytest against FastAPI, using a disposable Postgres (or
  SQLite-for-tests) fixture; contract tests per endpoint including auth
  failure cases.
- Sync client: unit tests mocking the HTTP layer — success path (new
  version applied locally), unchanged-version no-op, server-unreachable
  fallback, unregistered-PC no-op.
- No changes to the existing 180+ test DOKScanner.exe suite's current
  behavior; the sync client is additive and covered by its own new
  tests.
- Dashboard: component tests for the project/template CRUD forms and
  push action; a smoke test hitting a running backend for the
  register → push → PC-pulls-config round trip.

## Phases 2-4 (outlined only, not detailed here — each gets its own spec
when its turn comes)

- **Phase 2 — Live status monitoring**: richer `POST /pcs/{id}/heartbeat`
  payload (current job, queue depth, last error); dashboard fleet view.
- **Phase 3 — Aggregated reporting**: PCs push periodic QC/rejection/
  rescan/audit summaries; dashboard rolls up across the fleet. Overlaps
  with Enhancement Plan §3.9 (Remote Quality Monitoring) and the
  location-dimension half of §4.6.
- **Phase 4 — Centralized user/role management**: central server becomes
  source of truth for operator accounts instead of per-PC RBAC; touches
  `core/rbac.py` and needs careful migration design for existing local
  users. Deliberately last because it's the most security-sensitive
  change.

## Open questions for the implementation plan (not blocking this spec)

- Same repo (new top-level dir) vs. separate repo for `dokscanner-central`.
- Exact admin-dashboard auth mechanism (session cookie vs JWT).
- Config poll interval default and whether it should be admin-configurable
  per PC.
- Whether `POST /pcs/register` is triggered manually by an admin from the
  installer/admin panel, or fully automatic on first run against a
  known server URL.
