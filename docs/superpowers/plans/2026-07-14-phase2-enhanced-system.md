# DOK Scanner — Phase 2 Enhanced System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver Phase 2 of DOK_HLD_v2 (§18, weeks 5–10): real authentication (bcrypt), encryption at rest (SQLCipher + DPAPI keystore), scheduled backup with restore, admin panel, workflow designer, and vendor scanner SDK scaffolding.

**Architecture:** Everything extends Phase 1 seams: hashing swaps inside `core/rbac.py` only; encryption is a `Database` constructor parameter backed by a new DPAPI keystore; backup is a standalone service module; the designer edits the same project JSON `ProfileManager` already versions; SDK sources implement the existing `BaseScannerSource` ABC.

**Tech Stack additions:** bcrypt · sqlcipher3-wheels (SQLCipher AES-256) · pywin32 (DPAPI via win32crypt) · cryptography (Fernet backup encryption).

## Global Constraints

- Still 100% offline; all new deps bundle into the EXE.
- Encryption applied at database creation; a plaintext Phase 1 DB is migrated once via `sqlcipher_export` on first encrypted launch.
- Legacy sha256 password hashes upgrade to bcrypt transparently on next successful login.
- Every new UI element role-gated via `current_user.can(...)`.
- All modules keep graceful degradation: missing DPAPI/sqlcipher/vendor DLL → clear warning + safe fallback, never a crash.

## Tasks

### Task 1: bcrypt authentication (`core/rbac.py`)
- `hash_password` → bcrypt (cost 12); `verify_password` accepts bcrypt AND legacy salted-sha256; `Session.login` re-hashes legacy hashes on success (self-migrating).
- `change_password(db, username, old, new)`, `create_user`, `set_user_active`, `set_user_role` helpers with permission checks.
- Tests: bcrypt round-trip; legacy hash still logs in and is upgraded in DB; wrong old password rejected.

### Task 2: DPAPI keystore (`data/keystore.py`)
- `KeyStore(path)` — `get_or_create() -> bytes(32)`; key material protected with `win32crypt.CryptProtectData` (machine+user scope); plaintext-file fallback with loud warning if pywin32 missing.
- Tests: create/reload stable; file on disk is not the raw key (when DPAPI available).

### Task 3: SQLCipher database encryption (`data/db.py`)
- `Database(path, key: bytes | None)` — when key given and sqlcipher3 importable: sqlcipher connection + `PRAGMA key`; else plain sqlite3 with warning.
- `migrate_to_encrypted(plain_path, enc_path, key)` using `sqlcipher_export`; `is_encrypted(path)` sniffing the SQLite magic header.
- Tests: encrypted DB unreadable by plain sqlite3; wrong key fails; migration preserves rows; header check.

### Task 4: Backup & restore (`data/backup.py`)
- `BackupService(db, sources: list[Path], dest_dir, keystore, interval_hours)` — `run_backup()` → timestamped `.dokbak` (zip of consistent DB snapshot via SQLite backup API + project configs + file store) encrypted with Fernet (key from keystore); `restore_backup(archive, out_dir, key)`; `start()/stop()` scheduler thread; retention (keep last N).
- Tests: backup→restore round-trip byte-identical DB; archive is not a readable zip without key; retention prunes.

### Task 5: Admin panel (`ui/admin_panel.py`)
- Users tab: table of users, add-user dialog (role combo), activate/deactivate, reset password — all through Task 1 helpers, gated by `user.manage`.
- Backup tab: destination picker, interval spinner, Run backup now, last-result label — gated by `config.backup`.
- Wire into MainWindow (replaces placeholder Projects tab content alongside designer).

### Task 6: Workflow designer (`ui/workflow_designer.py`)
- Project selector + Clone + version history/rollback (ProfileManager backend).
- Editors bound to project JSON: feature toggles, separation strategies, QC stage toggles + thresholds, scanner block, export format/template/output path, delivery local folder.
- Rule builder: rules list + editor dialog building condition checks (field/op/value rows, AND/OR) and actions (type/value rows) → emits the exact Phase 1 DSL JSON, validated by `RuleEngine` before save.
- Save = `ProfileManager.save` (auto version + archive). Gated by `project.edit`.
- Tests (offscreen): edits round-trip into saved JSON; invalid rule rejected; rollback restores.

### Task 7: Vendor scanner SDK scaffolding (`scanning/kodak_source.py`, `alaris_source.py`, `czur_source.py`, `viisan_source.py`)
- Each implements `BaseScannerSource`; constructor takes optional DLL path; without the vendor DLL `list_devices()==[]` and `connect` raises `ScannerUnavailable` (HLD §20 TWAIN/WIA fallback). Shared `scanning/vendor_sdk.py` helper for DLL discovery in `vendor_sdks/`.
- Registry `scanning/sources.py`: name → source class, used by the scan wizard's source combo.
- Tests: all four degrade gracefully; registry lists twain/wia/file + 4 vendors.

### Task 8: Integration (`main.py`, `ui/main_window.py`)
- Startup: keystore key → encrypted DB (auto-migrate plaintext DB if found), BackupService started from settings, Admin + Designer tabs (role-gated).
- Update requirements.txt + README; full suite green.

## Verification
- `pytest` green including new suites; manual launch: login as admin → Admin/Designer tabs present; DB file starts with SQLCipher salt (not "SQLite format 3"); backup file created and restorable.
