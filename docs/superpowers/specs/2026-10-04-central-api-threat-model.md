# Central API - Threat Model (SDLC A.6)

**Scope:** the `central/` FastAPI service as built in Phase 1 (Tasks 1-9 of the
central-api plan). **Method:** STRIDE-style walk of every endpoint and trust
boundary, with each mitigation tied to the file that implements it and the
test that pins it. All test files are under `tests/central/`.
**Status of this document:** describes the API as built, not as originally
planned. Where the pre-build skeleton differed (lockout behaviour, session
revocation, code redemption) this document follows the code.

## 1. Assets

| Asset | Why it matters | Where it lives |
|---|---|---|
| Admin passwords | Full control of fleet configuration | `admin_users.password_hash` (SHA-256 pre-hash, then bcrypt) |
| Admin session cookie (`dok_central`) + CSRF token | Acts as an admin for up to 8 h | Browser; signed (not encrypted) Starlette cookie |
| PC API keys (`dsk_...`) | Lets a PC pull its configs | `pcs.api_key_hash` (SHA-256 hex); plaintext only at the PC |
| Enrollment codes | One-time right to mint a PC key | `enrollment_codes.code_hash` (SHA-256 hex) |
| `CENTRAL_SESSION_SECRET` | Forges admin session cookies if leaked | Environment of the uvicorn process |
| Project / template configs | Drive scanning behaviour on every PC; contain PC-local sections such as delivery and export paths | `projects.config_json`, `project_templates.config_json`, `config_versions` |
| Audit trail | Evidence of who changed/pulled what | `audit_events` |
| Database credentials / DB itself | Everything above | `CENTRAL_DATABASE_URL` (Postgres in production) |

Out of scope assets: scanned documents. Central never receives them.
Credentials for delivery channels (SFTP passwords, keys) are intentionally
kept off central (see T-8).

## 2. Attack surface

All routes are defined in `central/routes_*.py` and `central/app.py`. OpenAPI,
Swagger and ReDoc are disabled (`app.py`; pinned by `test_app.py::test_api_docs_are_not_exposed`).

| Endpoint | Auth | Notes |
|---|---|---|
| `GET /healthz` | none | Returns `{"status":"ok"}`, no data |
| `POST /pcs/register` | none by design | Gated by a one-time, expiring, hashed enrollment code |
| `POST /pcs/{pc_id}/heartbeat` | Bearer key bound to `pc_id` (`deps.require_pc`) | Refreshes `last_seen_at` only |
| `GET /pcs/{pc_id}/config` | Bearer key bound to `pc_id`; optional `X-Operator-Id` (audit only, unauthenticated) | Returns only that PC's assigned projects; writes one audit row per call |
| `POST /admin/login` | none (credential check) | Sets session cookie; returns CSRF token |
| `GET /admin/me` | session (CSRF on non-GET) | Safe method, no CSRF |
| `POST /admin/logout` | session (CSRF on non-GET) | Bumps `AdminUser.session_version` |
| `POST /admin/pcs`, `GET /admin/pcs`, `POST /admin/pcs/{id}/enrollment-code`, `DELETE /admin/pcs/{id}/key` | session (CSRF on non-GET) | Router-level `require_admin` + `require_csrf` |
| `PUT/GET /admin/projects[/{id}]`, `GET .../versions[/{n}]`, `POST .../push`, `DELETE .../assignments/{pc_id}` | session (CSRF on non-GET) | Router-level dependencies in `routes_admin_config.py` |
| `PUT/GET /admin/templates[/{id}]`, `GET .../versions[/{n}]` | session (CSRF on non-GET) | Same router |
| `GET/PUT /admin/settings` | session (CSRF on non-GET) | Global poll interval (30..86400, strict int) |

Every admin route other than `/admin/login`, `/admin/me`, `/admin/logout`
(which declare their own dependencies) sits behind a **router-level**
`dependencies=[Depends(require_admin), Depends(require_csrf)]`, so a new
route added to those routers is authenticated by default. A route-enumeration test (`test_route_guards.py`) walks the app's routes and asserts every `/admin` route except login is 401 unauthenticated and every non-GET one is 403 without the CSRF header (it also asserts the route list has >= 20 entries so it cannot silently shrink).

Out-of-band surface: `python -m central.manage create-admin` (operator CLI,
needs shell + DB access; enforces 12-character minimum password).

## 3. Trust boundaries

```
Admin browser ---\                                    /--> Postgres
                  >--> reverse proxy (TLS, body cap,  >
Scanning PCs  ---/      rate limit; NOT in this repo) \--> uvicorn / central app
```

1. **Internet/LAN to reverse proxy.** TLS termination, request-body size
   limits and rate limiting are assumed to be done here. The app has no TLS
   and no rate limiting of its own (see section 6).
2. **Proxy to app.** The app trusts nothing in the request: all bodies are
   validated by Pydantic; the global handler in `app.py` returns only
   `type/loc/msg` (no echoed input).
3. **Unauthenticated to PC-authenticated.** Crossed only by redeeming an
   enrollment code (`POST /pcs/register`).
4. **PC to PC.** A valid key authorises exactly one `pc_id` (path-bound).
5. **Unauthenticated to admin.** Crossed only by `POST /admin/login`.
6. **App to database.** SQLAlchemy ORM / Core constructs only, no raw SQL
   strings in `central/`. The DB is trusted; it holds configs in clear.
7. **Central to scanning PCs (data flow).** Configs flow down; PCs are
   trusted to apply them but credentials never flow up (T-8).

## 4. Threats and mitigations (STRIDE)

| # | Cat. | Threat | Mitigation (as built) | Implementation | Pinning test(s) |
|---|---|---|---|---|---|
| T-1 | S | Admin password guessing / user enumeration | bcrypt over a SHA-256 pre-hash (any length safe, no 72-byte truncation); 5 failures set a 15 min lock; **lockout is indistinguishable**: a locked account returns the same `401 invalid credentials` as a wrong password or unknown user (never 429) and still burns a bcrypt check so timing is equalised; the failed-login counter is incremented and read back in ONE `UPDATE ... RETURNING` statement and the lock decision uses the returned value (not a cached ORM object), so N parallel failures cannot overshoot the threshold; lone surrogates cannot cause a 500 that would reveal user existence | `security.py` (`_prehash`, `burn_password_check`), `routes_admin_auth.py` (`login`, `record_failed_login`) | `test_admin_auth.py::test_wrong_password_and_unknown_user_look_identical`, `::test_lockout_after_repeated_failures`, `::test_successful_login_resets_failure_count`, `::test_record_failed_login_atomic` (increments are not lost), `::test_lockout_decision_uses_db_value_not_stale_cached_object` (lock fires on the DB value, fails on the old cached-object logic), `::test_fifth_failure_writes_admin_locked`, `::test_login_rejects_surrogate_in_password`, `::test_login_rejects_surrogate_in_username`; `test_security.py::test_long_and_multibyte_passwords_work_and_stay_significant`, `::test_burn_password_check_is_always_false`, `::test_lone_surrogates_do_not_crash_or_enumerate` |
| T-2 | S / E | Unauthenticated access to admin routes | Router-level `require_admin` on `routes_admin_pcs` and `routes_admin_config`; `require_admin` rejects missing cookie, unknown user or stale `session_version` with 401 | `deps.py::require_admin`, router declarations | `test_route_guards.py::test_every_admin_route_requires_login`, `test_admin_pcs.py::test_pc_endpoints_require_login`, `test_admin_auth.py::test_me_requires_login`, `test_admin_config.py::test_project_writes_need_login_and_csrf`, `::test_template_versions_unauthenticated_401`, `::test_template_version_detail_unauthenticated_401` |
| T-3 | T / E | CSRF on admin mutations | `SameSite=strict` cookie, `Secure` by default (`CENTRAL_COOKIE_SECURE`), `HttpOnly` (Starlette default); per-session CSRF token compared with `hmac.compare_digest` on every non-safe method, router-level | `app.py` (SessionMiddleware), `deps.py::require_csrf` | `test_route_guards.py::test_every_admin_mutation_requires_csrf`, `::test_csrf_header_must_match`; `test_admin_auth.py::test_login_cookie_is_secure_when_cookie_secure_enabled`, `::test_logout_requires_csrf_and_ends_session`, `::test_wrong_csrf_token_rejected`, `::test_login_sets_secure_cookie_flags`; `test_admin_pcs.py::test_mutations_need_csrf`; `test_admin_config.py::test_template_without_csrf_is_403` |
| T-4 | S | Session fixation / stale or stolen cookie | New session on every login (`session.clear()` then populate). Cookie is signed, so server-side revocation is provided by `AdminUser.session_version`: **bumped on logout** (invalidates every cookie for that user), checked on each request. 8 h max age. **Not bumped on lockout** (design choice, see section 6: a stolen cookie survives a lockout until logout or expiry) | `routes_admin_auth.py` (`login`, `logout`), `deps.py::require_admin` | `test_admin_auth.py::test_session_invalidation_on_logout`, `::test_session_per_user_independent`, `::test_lockout_does_not_invalidate_other_sessions` |
| T-5 | S / I | Stolen or rogue PC key; key at rest | Per-PC random key (`dsk_` + 256 bits), only SHA-256 stored; admin can revoke (`DELETE /admin/pcs/{id}/key` nulls the hash and also deletes the PC's pending enrollment codes, so a leaked unused code cannot mint a new key); re-enrolling replaces the old key; revoked key is 401 | `security.py`, `routes_admin_pcs.py::revoke_key`, `routes_pcs.py::register` | `test_pcs.py::test_revoked_key_is_401`, `::test_reenrolling_replaces_the_old_key`, `::test_heartbeat_without_or_with_garbage_key_is_401`; `test_admin_pcs.py::test_revoke_key_clears_hash_and_404s_for_unknown`, `::test_revoke_key_also_kills_pending_enrollment_code`; `test_security.py::test_api_keys_are_unique_and_prefixed`, `::test_hash_secret_is_deterministic_sha256_hex` |
| T-6 | S / E | Enrollment code replay, brute force, race | Code is 96-bit random, SHA-256 at rest, 60 min TTL, one live code per PC (issuing replaces earlier). **Redemption is a single atomic conditional `UPDATE ... WHERE code_hash=? AND used_at IS NULL AND expires_at>=now`**; `rowcount != 1` returns a uniform `400 invalid or expired enrollment code` (unknown, used, expired and malformed bodies are indistinguishable) | `routes_pcs.py::register`, `app.py` (validation handler maps `/pcs/register` to uniform 400), `routes_admin_pcs.py::issue_code` | `test_pcs.py::test_code_is_single_use`, `::test_unknown_code_is_rejected_like_a_used_one`, `::test_expired_code_is_rejected_and_mints_no_key`, `::test_concurrent_code_redemption_is_atomic` (simulates a winner claiming first), `::test_register_with_lone_surrogate_code_reaches_hash_secret`; `test_admin_pcs.py::test_new_enrollment_code_replaces_the_old_one`; `test_security.py::test_enrollment_codes_are_long_and_unique` |
| T-7 | E / I | One PC reading or acting for another | `require_pc` looks the key up by hash, then requires `pc.id == path pc_id` (else 403); config query is filtered by that PC's assignments | `deps.py::require_pc`, `routes_pcs.py::get_config` | `test_pcs.py::test_key_of_another_pc_is_403`; `test_config_pull.py::test_pull_needs_valid_key_for_that_pc`, `::test_pc_never_sees_projects_assigned_to_other_pcs` |
| T-8 | I | Secrets leaking into the central DB via configs | Every config write (projects and templates) runs `check_config`: normalised key names (lowercase, non-alphanumerics stripped) are matched by substring against password/passwd/passphrase/secret/token/apikey/privatekey (`keyfile`/`key_file` allowed: paths); exact normalised keys `pwd`, `pass`, `credential(s)`, `authorization` are also flagged (exact, not substring, so `passthrough` is fine); string values are scanned for URL userinfo (including an empty user, `://:pw@`), ODBC/query-string `password=`/`pwd=`/`token=`/`secret=`/`sig=`/`apikey=`... pairs, `Basic`/`Bearer` header values and PEM `PRIVATE KEY` headers. The scan is a heuristic (see section 6); iterative traversal with a **depth cap of 64**. Violations are 422 and nothing is stored. Built-in templates are checked in tests | `config_rules.py`, `routes_admin_config.py::_check` | `test_config_rules.py::test_finds_nested_credential_keys_case_insensitively`, `::test_finds_credentials_inside_lists`, `::test_credential_key_detection_with_variants`, `::test_credential_url_userinfo_detection`, `::test_credential_pem_key_detection`, `::test_exact_credential_keys_are_flagged`, `::test_exact_key_match_is_not_a_substring_match`, `::test_credential_value_patterns_are_flagged`, `::test_harmless_values_are_not_flagged`, `::test_regression_default_project_config_passes`; `test_admin_config.py::test_credentials_in_config_are_rejected_and_not_stored`, `::test_template_with_credentials_is_rejected`, `::test_builtin_template_passes_credential_check` |
| T-9 | D | Deeply nested JSON / recursion crash | Depth cap 64 in the scanner (`ValueError` to 422). Very deep bodies are rejected by the framework's JSON/body parser with 400/422 before the route runs; the depth cap handles what gets past it. (The `RecursionError` handlers in `routes_admin_config.py` and `app.py` are defensive leftovers and are NOT relied on as a mitigation.) | `config_rules.py`, `routes_admin_config.py`, `app.py` | `test_config_rules.py::test_deeply_nested_dict_rejected`, `::test_deeply_nested_list_rejected`, `::test_moderately_nested_config_accepted`; `test_admin_config.py::test_project_deeply_nested_500_via_http_returns_422`, `::test_template_deeply_nested_500_via_http_returns_422`, `::test_project_deeply_nested_5000_via_http_returns_safe_status`, `::test_template_deeply_nested_5000_via_http_returns_safe_status` |
| T-10 | D | Oversized configs / bodies | 256 KB cap (`MAX_CONFIG_BYTES`) on the serialised config; bounded field lengths (names 200, category 100, login 100/200, code 8..100, poll interval 30..86400). **The cap is applied after the body has been parsed**, so it limits storage, not the memory/CPU spent parsing; the reverse proxy MUST cap request body size | `config_rules.py::check_config`, Pydantic models | `test_admin_config.py::test_oversized_config_is_422`, `test_config_rules.py::test_check_config_rejects_credentials_and_oversize`; `test_push_settings.py::test_poll_interval_out_of_range_or_non_integer_rejected`; `test_admin_auth.py::test_login_over_length_password_not_echoed` |
| T-11 | R / T | Audit log forging via client-supplied text | `X-Operator-Id` is stripped of control characters and capped at 100 chars before being stored (JSON-encoded detail also escapes). Audit rows written for config changes (including template renames, `template_updated`), pushes, settings, PC lifecycle, every config pull and admin authentication (`admin_login`, `admin_login_failed`, `admin_locked`, `admin_logout`, `admin_created` with actor `cli`). A failed login for an unknown username stores no username; failures write the audit row before the 401. The same auth events go to the `dokscanner.central` logger (INFO login/logout, WARNING failed/locked; never passwords) for SIEM collection | `security.py::sanitize_log_value`, `audit.py`, `routes_pcs.py::get_config` | `test_admin_auth.py::test_login_success_is_audited`, `::test_wrong_password_is_audited_with_username_and_one_warning`, `::test_unknown_username_is_audited_without_any_username`, `::test_logout_is_audited`, `::test_create_admin_is_audited_as_cli_without_password`; `test_admin_config.py::test_template_rename_without_config_change_is_audited`; `test_security.py::test_sanitize_log_value_strips_control_chars_and_truncates`, `::test_audit_record_adds_row_without_committing`; `test_config_pull.py::test_operator_header_is_audited_and_capped`, `::test_non_ascii_operator_is_stored_unmangled_by_sanitizer`, `::test_each_pull_writes_one_config_pull_audit_row_with_served_versions`, `::test_rejected_pulls_write_no_audit_and_do_not_touch_last_seen` |
| T-12 | R | **Residual:** `X-Operator-Id` identity is self-asserted | Documented as audit-only metadata, never used for access decisions; the authenticated actor in the audit row is always the PC | `routes_pcs.py::get_config` docstring | `test_config_pull.py::test_absent_operator_header_is_stored_empty` (behaviour only; no authenticity is claimed) |
| T-13 | T | Lost updates / duplicate versions under concurrency | PUT upserts retry once on `IntegrityError` (unique `(kind, owner_id, version)`), then return 409 (never 500); duplicate PC name is 409; a push that collides with a concurrent insert re-reads the existing assignments and retries (max 3 attempts, then 409), so every requested PC ends up assigned and the audit/response list exactly those PCs; identical resubmit does not bump the version; history is append-only | `routes_admin_config.py` (`put_project`, `put_template`, `push_project`), `routes_admin_pcs.py::create_pc`, `versioning.py` | `test_admin_config.py::test_project_create_race_retries_and_succeeds`, `::test_template_create_race_retries_and_succeeds`, `::test_project_update_race_writes_next_version`, `::test_template_update_race_writes_next_version`, `::test_project_concurrent_both_races_returns_409`, `::test_template_concurrent_both_races_returns_409`, `::test_identical_resubmit_does_not_bump_version`; `test_admin_pcs.py::test_create_pc_duplicate_at_insert_time_returns_409`; `test_push_settings.py::test_concurrent_push_of_same_assignment_is_idempotent_success`, `::test_concurrent_push_overlap_still_assigns_every_requested_pc` |
| T-14 | T | SQL injection | ORM only; code review + Bandit (0 findings in `central/`, see SAST report). Path ids are additionally constrained by regex `^[A-Za-z0-9-]{1,36}$` for projects/templates | all `central/*.py` | Not pinned by a test: code review and the SAST scan (docs/security/SAST_FINDINGS_REPORT.md); `test_admin_config.py::test_malformed_project_id_is_422` only pins the id format |
| T-15 | I | Information disclosure in errors | Validation errors return only `type/loc/msg` (no input echo); `/pcs/register` validation failures map to the uniform 400; docs/OpenAPI off | `app.py` | `test_admin_auth.py::test_login_over_length_password_not_echoed`, `::test_login_missing_password_returns_422`; `test_app.py::test_api_docs_are_not_exposed` |
| T-16 | D | Invalid Unicode (lone surrogates) causing 500s at DB bind | `SafeStr` validator on user text, UTF-8 validator on login fields, `surrogatepass` in hashing | `textsafe.py`, `routes_admin_auth.py`, `security.py` | `test_textsafe.py::test_lone_surrogate_rejected`, `::test_unicode_escapes_rejected`; `test_admin_pcs.py::test_post_pc_with_lone_surrogate_returns_422`; `test_security.py::test_hash_secret_with_lone_surrogate_does_not_raise`, `::test_hash_secret_lone_surrogates_differ` |
| T-17 | S | Weak or missing session secret | `Settings.from_env` refuses to start with a `CENTRAL_SESSION_SECRET` under 32 chars or a missing DB URL | `settings.py` | `test_app.py::test_settings_from_env_requires_strong_secret`, `::test_settings_from_env_reads_values`; `test_roundtrip.py::test_create_app_from_env_rejects_missing_or_short_secret` |
| T-18 | I / T | Weak admin passwords at creation | CLI enforces 12-character minimum and unique username | `manage.py::create_admin` | `test_admin_auth.py::test_create_admin_enforces_password_length_and_uniqueness` |

End-to-end happy path (register, push, pull, update, revoke) is pinned by
`test_roundtrip.py::test_register_push_pull_update_revoke_roundtrip`.

## 5. Design notes worth knowing when reviewing

- **Sessions are signed cookies, not server-side sessions.** Starlette's
  `SessionMiddleware` signs (does not encrypt) the cookie; contents are
  `admin_id`, `sv` and `csrf`. Revocation is achieved by comparing `sv` with
  `AdminUser.session_version` on every request.
- **API key lookup is an indexed equality on the SHA-256 digest**, not a
  constant-time compare. This is acceptable because keys have 256 bits of
  entropy and the attacker never sees the digest.
- **401 vs 403 on PC routes** tells the holder of a valid key that the key is
  real but for another PC. It leaks nothing to a holder of no key.

## 6. Residual risks and follow-ups

Ordered roughly by priority. None block Phase 1 go-live behind a properly
configured reverse proxy; items 1-3 are conditions of that configuration.

1. **No rate limiting in the app** on `POST /pcs/register`, on PC endpoints
   (beyond key secrecy) or on `POST /admin/login` (beyond per-account lockout).
   Do this at the reverse proxy. Code entropy (96 bits, 60 min) makes online
   brute force of enrollment codes infeasible, but unthrottled requests still
   cost DB writes.
2. **No TLS in the app.** Bearer keys, session cookies and CSRF tokens are
   only protected by the proxy's TLS. `CENTRAL_COOKIE_SECURE=0` exists for
   local development and must never be set in production.
3. **Request body size is only capped after parsing** (256 KB config cap is
   post-parse). The proxy must enforce a request-body limit (suggest 512 KB).
4. **Lockout is a denial-of-service lever.** Anyone who knows an admin
   username can lock that admin out for 15 minutes by sending 5 bad logins,
   repeatably. Indistinguishable 401s prevent enumeration but do not prevent
   this. Mitigate with proxy-level per-IP throttling and an unpredictable
   admin username.
5. **A stolen session cookie survives a lockout.** `session_version` is bumped
   only on logout, deliberately (so an attacker cannot force-logout a user by
   failing logins). Consequence: lockout does not evict an already-hijacked
   session; it remains valid until the admin logs out or the 8 h expiry. There
   is no admin-initiated "revoke all sessions" or password-change flow yet.
6. **Single admin role.** Every admin can do everything (including revoking
   keys and editing all configs). `role` is stored but not enforced. Per-admin
   permissions are out of scope for Phase 1.
7. **`audit_events` retention is undefined, and it grows one row per PC poll**
   (`config_pull`; heartbeat writes none). With N PCs polling every 600 s this
   is N x 144 rows/day with no pruning, archiving or index beyond the PK.
   Needs a retention/rotation policy before a large fleet is attached.
8. **`X-Operator-Id` is self-asserted and unauthenticated.** It is recorded for
   audit convenience only; any holder of a PC key can claim any operator name.
   Operator-level accountability needs a real operator login (Phase 2+).
9. **No migrations.** Schema is created by `Base.metadata.create_all`; there is
   no Alembic history, so schema changes after first deployment need a manual
   plan. Add Alembic before the first production schema change.
10. **Postgres behaviour is untested.** The whole suite runs on SQLite. The
    atomic conditional UPDATE (code redemption), atomic `failed_count`
    increment, the retry-once-on-IntegrityError upsert and the idempotent
    concurrent-push handling are designed for READ COMMITTED Postgres races
    but have only been exercised with simulated races on SQLite. A real
    Postgres integration run with concurrent clients is a follow-up.
11. **The central DB holds full project configs**, including PC-local sections
    such as delivery and export paths and folder layouts. The credential scan
    blocks secrets by pattern, but it is a heuristic (a secret under an
    innocuous key name, or an encoded value, would pass). Treat the central DB
    as holding sensitive site-structure data: restrict DB access, encrypt
    backups, and keep an organisational rule that credentials never go in
    configs.
12. **Enrollment codes are shown once in the admin response**; they are not
    retrievable later (only the hash is stored). Operators who lose one must
    issue another (which invalidates the old one). Intended.
13. **Admin GET endpoints do not require CSRF** (safe methods). They return
    configs but are protected by SameSite=strict and the session; acceptable.
14. **Route-enumeration coverage is now in place** (`test_route_guards.py`),
    but it only sees routes registered on the app; it does not replace review
    of new routers.
15. **Untested Postgres races on first-insert paths.** `set_poll_interval`
    (first insert of the setting row) and `issue_code` (delete + insert of the
    PC's code) are check-then-write sequences that can collide under
    concurrent requests on Postgres; neither is covered by a concurrency test.
16. **The credential scanner is heuristic** (key-name and value-pattern
    matching). A secret under an innocuous key and an encoded value still
    passes; see item 11.
17. **Concurrency tests are SQLite-only and simulated.** A Postgres CI job
    with genuinely concurrent clients is the recommended follow-up (it would
    also cover items 10 and 15).
18. **Missing admin operations** (password reset/disable, revoke all sessions,
    PC deletion, audit-log read API) are not built; lockout does not evict
    live sessions (item 5). Dependencies are pinned in
    `central/requirements.txt`; Starlette's SessionMiddleware behaviour (cookie
    re-signed only when the session changes) underpins the absolute 8 h expiry
    and must be re-checked on any Starlette upgrade.

## 7. Client side (DOKScanner.exe)

The PC-side sync client (`core/central_client.py`, `central_sync.py`,
`central_state.py`, `config_overlay.py`, `safe_paths.py`) treats the central
server and everything it returns as untrusted input. End-to-end tests against
the real app: `tests/test_central_integration.py`.

**Mitigations**

- Transport: HTTPS only (plain HTTP solely to loopback when an explicit
  environment switch is set); TLS always verified; redirects never followed;
  response body capped at 5 MB; non-JSON, non-object, NaN/Infinity and
  malformed replies are rejected as "unavailable".
- Credentials: the API key is stored DPAPI-protected, sent only in the
  Authorization header, and never logged or put in error text. `pc_id` and the
  key must be printable ASCII, 1-256 chars. Central never sends credentials
  (the API rejects them with 422); delivery credentials live only in a
  PC-local section (`delivery`) that central values never overwrite.
- `project_id` safety: it becomes a file name, so only safe ids are accepted
  (no separators, `..`, control characters or Windows reserved names such as
  CON/NUL/COM1); unsafe entries are skipped, not fatal.
- Local-only sections: `scanner`, `export` and `delivery` are never taken from
  central; the PC keeps its own. A project new to the PC is seeded from the
  project the PC used before enrollment (else app defaults), and an operator
  can edit these three sections for a managed project in the Workflow Designer
  (nothing else is saved for it).
- Rule-action path guard: `set_folder` / `set_filename` values containing
  `..`, absolute paths, drive letters or control characters cause the project
  to be skipped. `safe_join` is applied again at export and local delivery (and the same
  sub-path check at SFTP), so a value that slipped through cannot leave the root.
- Availability: the startup sync uses a 5 s limit per socket operation
  (connect/read; name resolution is not covered, so a slow DNS lookup can
  delay startup longer) and never blocks scanning;
  an unreachable server leaves the last-known projects untouched. A revoked key
  (401/403) is persisted as `auth_failed` and polling stops until
  re-enrolment. Updates apply to the next scan batch (the pipeline resolves a
  batch's project once, when the batch starts, and a running batch is never
  changed).
- Per-batch project resolution: `RuntimeResolver` reads the stored project and
  validates it each time a batch starts. `ProjectUnavailable` messages carry
  only field paths and the project id, never configuration values (the chained
  cause is neither shown nor logged). Crash recovery resolves per batch and
  fails closed: an unusable project is not run under another project's
  settings.
- Consistency: state writes are generation-guarded, so a sync that started
  before a register/disconnect cannot overwrite the new registration; apply
  and register/disconnect are serialised by one lock, with no network call
  held under it. Server-supplied text is stripped of control characters and
  truncated before it reaches the audit trail or logs.

**Residual risks**

1. A compromised central (or a stolen admin session) can push arbitrary
   processing configuration, including rules, to every assigned PC. The path
   guards limit the damage to the PC's own output tree, not to what the
   configuration does with documents. Concretely, through rules and config it
   can still: bypass QC (`skip_stage`), approve documents without review
   (`set_validation_status: auto_approved`), and change `features.*` and
   `ocr.*` settings. `trigger_delivery` can only use channels that are already
   configured locally; central cannot add a destination or credentials.
   Credentials of local channels stay in the PC-local `delivery` section.
2. Configs are not signed; TLS to the server is the only integrity control.
3. `X-Operator-Id` is self-asserted (see item 8 above).
4. A persistently invalid project is re-skipped, and re-audited, on every
   poll; this audit noise is not throttled.
5. (Removed 2026-10-12.) A change now applies to the next scan batch and each
   batch runs under its own project; the Index tab zones/OCR mode and the
   CD/DVD burn tab still follow the startup project (see
   `docs/ENHANCEMENT_PLAN_STATUS.md`).
6. The Workflow Designer decides "managed" when the operator clicks Save; a
   sync that applies a new central version between that check and the file
   write is a narrow window. Only the local sections are saved for a managed
   project and a sync never overwrites them, so nothing local is lost; the
   next sync restores central's content.
7. Enrollment is per Windows account: the key is DPAPI-protected (user-bound)
   and the data lives under that user's `%LOCALAPPDATA%`, so another Windows
   account on the same PC must enrol separately.
