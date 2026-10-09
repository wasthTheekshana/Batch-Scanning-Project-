# Central Sync Client (Phase 1, Plan 2 of 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let DOKScanner.exe register with the central service, pull the projects an admin assigned to this PC, and apply them locally — without ever blocking scanning and without touching the PC's own delivery/export/scanner settings.

**Architecture:** A stdlib-only HTTPS client (`core/central_client.py`) talks to the central API built in plan 1. A `SyncService` applies pulled projects through the existing `ProfileManager` (new `adopt()`), overlaying the PC-local sections (`scanner`, `export`, `delivery`) so central never overwrites them. A background `SyncWorker` thread polls on the server-supplied interval; an Admin-panel "Central" tab handles enrollment and status. Unregistered PCs behave exactly as today.

**Tech Stack:** Python 3.11, PyQt6, stdlib `urllib`/`ssl`/`threading`, existing SQLite/SQLCipher layer, Windows DPAPI via `data/keystore.py`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-29-central-admin-dashboard-design.md` (section "Decisions resolved 2026-10-04") and the as-built contract in `docs/superpowers/plans/2026-10-04-central-api.md` ("Cross-plan contracts" — read it first; this plan implements the client side of it).

## Global Constraints

- Backward compatible: a PC with no central registration behaves exactly as today (no network calls, no new UI side effects, existing tests unchanged).
- Scanning/QC/indexing is never blocked by central availability; any sync failure leaves the last-known-good local config in place.
- HTTPS only. `http://` is accepted only for loopback hosts (`127.0.0.1`, `localhost`, `::1`) and only when the dev switch `DOKSCANNER_CENTRAL_ALLOW_LOOPBACK_HTTP=1` is set. TLS certificates are always verified; redirects are never followed.
- No new third-party dependencies (use `urllib.request`, `ssl`, `json`, `threading`).
- The central API key is stored only via DPAPI (`data/keystore.py`), never in `central.json`, the database, logs, or audit entries.
- PC-local sections `scanner`, `export`, `delivery` are NEVER taken from central; they are kept from the local project, or seeded with local defaults on first pull.
- The client compares ONLY the outer per-project `version` returned by central; the `version` key inside `config` is the project file's own counter and is never used for sync decisions.
- A pulled `project_id` is used as a file name: it must match `^[A-Za-z0-9-]{1,36}$` or the entry is rejected (no path traversal).
- Server response bodies are capped at 5 MB; the poll interval is clamped to 30..86400 seconds (default 600).
- Python 3.11, PyQt6; follow the existing code style (type hints, `from __future__ import annotations`, module docstrings citing the spec section).
- End every commit message with the `Co-Authored-By` trailer given in the session's attribution rule.

## Cross-plan contract recap (client behaviour required by plan 1)

- Register: `POST {base}/pcs/register {"code": ...}` → `200 {pc_id, api_key, poll_interval_seconds}`; `400` = bad/used/expired code.
- Pull: `GET {base}/pcs/{pc_id}/config` with `Authorization: Bearer <key>` and optional `X-Operator-Id` (send ASCII, percent-encode anything else) → `{pc_id, poll_interval_seconds, projects:[{project_id,name,version,config}]}`.
- `401` = key missing/revoked/replaced → the PC must stop polling and show "re-enrollment required" (never loop). `403` = key belongs to another PC (misconfiguration) → treat like 401. `5xx`/network → keep local config, retry later.
- A project that disappears from `projects` was unassigned: keep the local project and its data, stop managing it.
- Templates are not delivered to PCs in Phase 1.

## Review Focus

- A malicious or compromised server sends a path-traversal `project_id` (`../../x`), a mismatched `config.project_id`, or an oversized/garbage body → nothing is written outside `projects/`, the other projects still apply, the app keeps running. (Tasks 3, 4, 5)
- The server is unreachable (DNS failure, timeout, TLS error, 5xx) at startup or mid-session → the app starts and scans normally on its last-known-good config; retries are bounded (≥ 2 minutes apart), never a tight loop. (Tasks 3, 5, 6)
- The PC's key was revoked/replaced (401/403) → polling stops, the state is persisted (survives restart), the Central tab shows a clear re-enrollment message, and a new enrollment code restores service. (Tasks 5, 6, 7)
- A pulled project fails the local schema (newer server) → that project is skipped with a recorded reason, its previous local version stays, the others still apply. (Task 5)
- Local-only sections survive updates, are seeded sensibly on first pull, and credentials/delivery targets can never arrive from central; an operator editing a centrally-managed project in the Workflow Designer is stopped before their edit is silently overwritten. (Tasks 4, 5, 7)

---

## File Structure

| File | Responsibility |
|---|---|
| `data/keystore.py` (modify) | Extract `protect_bytes`/`unprotect_bytes`; add `ProtectedBlob` |
| `core/central_state.py` (create) | `CentralSettings`, `CentralState`: `central.json` + DPAPI-protected API key |
| `data/db.py` (modify) | `central_projects` table + accessor methods |
| `core/profile_manager.py` (modify) | `ProfileManager.adopt()` + id safety check |
| `core/central_client.py` (create) | HTTPS client, transport, error types, response validation |
| `core/config_overlay.py` (create) | `validate_entry`, `overlay`, `default_local_sections`, `LOCAL_ONLY_KEYS` |
| `core/central_sync.py` (create) | `SyncResult`, `CentralSyncService`, `SyncWorker`, `choose_pipeline_profile` |
| `core/event_bus.py` (modify) | `central_synced` signal |
| `ui/central_tab.py` (create) | Admin-panel "Central" tab |
| `ui/admin_panel.py` (modify) | host the Central tab |
| `ui/workflow_designer.py` (modify) | block saving centrally-managed projects |
| `ui/main_window.py`, `main.py` (modify) | wiring, startup sync, shutdown |
| `tests/test_central_*.py`, `tests/test_config_overlay.py` (create) | one module per task |
| docs (modify) | central README, UAT checklist, threat model, status |

---

### Task 1: Protected secret storage and central state

**Files:**
- Modify: `data/keystore.py`
- Create: `core/central_state.py`
- Test: `tests/test_central_state.py`

**Interfaces:**
- Produces: `data.keystore.protect_bytes(secret: bytes, description: str) -> bytes`; `data.keystore.unprotect_bytes(data: bytes) -> bytes` (raises `RuntimeError` on unknown format / DPAPI unavailable); `ProtectedBlob(path, description="DOKScanner secret")` with `save(secret: bytes)`, `load() -> bytes | None`, `delete()`, `exists() -> bool`.
- Produces: `core.central_state`: constants `MIN_POLL=30`, `MAX_POLL=86400`, `DEFAULT_POLL=600`; `clamp_poll(value) -> int`; `@dataclass CentralSettings(server_url="", pc_id="", poll_interval_seconds=600, auth_failed=False, last_sync_at="", last_sync_status="")`; `CentralState(root: Path)` with `.settings`, `is_registered() -> bool`, `api_key() -> str | None`, `register_result(server_url, pc_id, api_key, poll_interval_seconds)`, `update(**fields)`, `clear()`.

- [ ] **Step 1: Write the failing tests**

`tests/test_central_state.py`:
```python
import json

import pytest

from core.central_state import (DEFAULT_POLL, MAX_POLL, MIN_POLL, CentralState,
                                clamp_poll)
from data.keystore import KeyStore, ProtectedBlob, protect_bytes, unprotect_bytes


def test_protect_roundtrip_and_unknown_format():
    assert unprotect_bytes(protect_bytes(b"s3cret", "test")) == b"s3cret"
    with pytest.raises(RuntimeError):
        unprotect_bytes(b"BOGUS\0abc")


def test_protected_blob_roundtrip_overwrite_delete(tmp_path):
    blob = ProtectedBlob(tmp_path / "keys" / "x.bin")
    assert blob.load() is None and not blob.exists()
    blob.save(b"one")
    assert blob.exists() and blob.load() == b"one"
    blob.save(b"two")
    assert blob.load() == b"two"
    blob.delete()
    assert blob.load() is None
    blob.delete()  # idempotent


def test_protected_blob_is_not_plaintext_on_disk_when_dpapi_available(tmp_path):
    blob = ProtectedBlob(tmp_path / "x.bin")
    blob.save(b"PLAINTEXT-MARKER-12345")
    raw = (tmp_path / "x.bin").read_bytes()
    if raw.startswith(b"DPAPI1\0"):
        assert b"PLAINTEXT-MARKER-12345" not in raw
    else:
        pytest.skip("pywin32/DPAPI not available in this environment")


def test_keystore_behaviour_unchanged(tmp_path):
    ks = KeyStore(tmp_path / "keys" / "master.bin")
    key = ks.get_or_create()
    assert len(key) == 32 and ks.get_or_create() == key


def test_clamp_poll():
    assert clamp_poll(5) == MIN_POLL and clamp_poll(10**9) == MAX_POLL
    assert clamp_poll(120) == 120 and clamp_poll("x") == DEFAULT_POLL
    assert clamp_poll(True) == DEFAULT_POLL


def test_fresh_state_is_unregistered(tmp_path):
    st = CentralState(tmp_path)
    assert not st.is_registered() and st.api_key() is None
    assert st.settings.poll_interval_seconds == DEFAULT_POLL


def test_register_result_persists_and_survives_reload(tmp_path):
    st = CentralState(tmp_path)
    st.register_result("https://c.example", "pc-1", "dsk_KEY", 90)
    st2 = CentralState(tmp_path)
    assert st2.is_registered() and st2.api_key() == "dsk_KEY"
    assert st2.settings.pc_id == "pc-1" and st2.settings.poll_interval_seconds == 90
    assert st2.settings.server_url == "https://c.example"


def test_api_key_never_written_to_central_json(tmp_path):
    st = CentralState(tmp_path)
    st.register_result("https://c.example", "pc-1", "dsk_SECRETKEY", 600)
    text = (tmp_path / "central.json").read_text(encoding="utf-8")
    assert "dsk_SECRETKEY" not in text and "api_key" not in text


def test_update_and_clear(tmp_path):
    st = CentralState(tmp_path)
    st.register_result("https://c.example", "pc-1", "k", 600)
    st.update(auth_failed=True, last_sync_status="auth failed (401)",
              poll_interval_seconds=5)
    assert st.settings.auth_failed and st.settings.poll_interval_seconds == MIN_POLL
    assert CentralState(tmp_path).settings.auth_failed is True
    st.clear()
    assert not st.is_registered() and not (tmp_path / "central.json").exists()
    assert CentralState(tmp_path).api_key() is None


def test_register_result_resets_auth_failed(tmp_path):
    st = CentralState(tmp_path)
    st.register_result("https://c.example", "pc-1", "old", 600)
    st.update(auth_failed=True)
    st.register_result("https://c.example", "pc-1", "new", 600)
    assert st.settings.auth_failed is False and st.api_key() == "new"


def test_corrupt_or_partial_json_falls_back_to_defaults(tmp_path):
    (tmp_path / "central.json").write_text("{not json", encoding="utf-8")
    assert CentralState(tmp_path).settings.server_url == ""
    (tmp_path / "central.json").write_text(
        json.dumps({"server_url": "https://x", "bogus_field": 1,
                    "poll_interval_seconds": "abc"}), encoding="utf-8")
    s = CentralState(tmp_path).settings
    assert s.server_url == "https://x" and s.poll_interval_seconds == DEFAULT_POLL


def test_registered_requires_the_key_file(tmp_path):
    st = CentralState(tmp_path)
    st.register_result("https://c.example", "pc-1", "k", 600)
    (tmp_path / "keys" / "central_api_key.bin").unlink()
    assert not CentralState(tmp_path).is_registered()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/test_central_state.py`
Expected: FAIL — `ImportError: cannot import name 'ProtectedBlob'`.

- [ ] **Step 3: Implement `data/keystore.py` changes**

First `grep -rn "unrecognised keystore\|pywin32 is unavailable" tests` and keep every existing KeyStore behaviour/message intact. Replace `KeyStore._store`/`_load` internals by delegating to two new module-level helpers, and add `ProtectedBlob`:

```python
def protect_bytes(secret: bytes, description: str) -> bytes:
    """DPAPI-protect `secret` (machine+user bound); plaintext fallback with a
    loud warning where pywin32 is missing (development only)."""
    dp = _dpapi()
    if dp is not None:
        blob = dp.CryptProtectData(secret, description, None, None, None, 0)
        return b"DPAPI1\0" + blob
    log.warning("pywin32 missing — storing secret WITHOUT DPAPI "
                "protection (development fallback only)")
    return b"PLAIN1\0" + secret


def unprotect_bytes(data: bytes) -> bytes:
    if data.startswith(b"DPAPI1\0"):
        dp = _dpapi()
        if dp is None:
            raise RuntimeError(
                "key was DPAPI-protected but pywin32 is unavailable")
        _desc, secret = dp.CryptUnprotectData(data[7:], None, None, None, 0)
        return secret
    if data.startswith(b"PLAIN1\0"):
        return data[7:]
    raise RuntimeError("unrecognised keystore format")


class ProtectedBlob:
    """A small secret (e.g. the central API key) stored like the master key."""

    def __init__(self, path: str | Path,
                 description: str = "DOKScanner secret"):
        self.path = Path(path)
        self.description = description
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def exists(self) -> bool:
        return self.path.exists()

    def save(self, secret: bytes) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_bytes(protect_bytes(secret, self.description))
        os.replace(tmp, self.path)

    def load(self) -> bytes | None:
        if not self.path.exists():
            return None
        return unprotect_bytes(self.path.read_bytes())

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)
```
`KeyStore._store(key)` becomes `self.path.write_bytes(protect_bytes(key, _DPAPI_DESCRIPTION))`; `KeyStore._load()` calls `unprotect_bytes(self.path.read_bytes())` and, to preserve its old message, re-raises an "unrecognised" error as `RuntimeError(f"unrecognised keystore format: {self.path}")`.

- [ ] **Step 4: Implement `core/central_state.py`**

```python
"""Local state for the central-sync feature: non-secret settings in
central.json plus the DPAPI-protected API key (spec: sync client)."""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from data.keystore import ProtectedBlob

log = logging.getLogger(__name__)

SETTINGS_FILE = "central.json"
KEY_FILE = "central_api_key.bin"
MIN_POLL, MAX_POLL, DEFAULT_POLL = 30, 86400, 600


def clamp_poll(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_POLL
    return max(MIN_POLL, min(MAX_POLL, value))


@dataclass
class CentralSettings:
    server_url: str = ""
    pc_id: str = ""
    poll_interval_seconds: int = DEFAULT_POLL
    auth_failed: bool = False
    last_sync_at: str = ""
    last_sync_status: str = ""


class CentralState:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._path = self.root / SETTINGS_FILE
        self._key = ProtectedBlob(self.root / "keys" / KEY_FILE,
                                  "DOKScanner central API key")
        self._lock = threading.Lock()  # worker thread + UI both write
        self.settings = self._load()

    def _load(self) -> CentralSettings:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except FileNotFoundError:
            return CentralSettings()
        except (OSError, ValueError) as e:
            log.warning("ignoring unreadable %s: %s", self._path, e)
            return CentralSettings()
        known = {f.name for f in fields(CentralSettings)}
        try:
            s = CentralSettings(**{k: v for k, v in data.items() if k in known})
        except TypeError:
            return CentralSettings()
        s.poll_interval_seconds = clamp_poll(s.poll_interval_seconds)
        return s

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self.settings), indent=2),
                       encoding="utf-8")
        os.replace(tmp, self._path)

    def is_registered(self) -> bool:
        s = self.settings
        return bool(s.server_url and s.pc_id and self._key.exists())

    def api_key(self) -> str | None:
        try:
            raw = self._key.load()
        except RuntimeError as e:
            log.error("cannot read the central API key: %s", e)
            return None
        return raw.decode("utf-8") if raw else None

    def register_result(self, server_url: str, pc_id: str, api_key: str,
                        poll_interval_seconds: int) -> None:
        with self._lock:
            self._key.save(api_key.encode("utf-8"))
            self.settings = CentralSettings(
                server_url=server_url, pc_id=pc_id,
                poll_interval_seconds=clamp_poll(poll_interval_seconds))
            self._save()

    def update(self, **changes) -> None:
        with self._lock:
            known = {f.name for f in fields(CentralSettings)}
            for k, v in changes.items():
                if k not in known:
                    raise AttributeError(f"unknown central setting: {k}")
                setattr(self.settings, k, v)
            self.settings.poll_interval_seconds = clamp_poll(
                self.settings.poll_interval_seconds)
            self._save()

    def clear(self) -> None:
        with self._lock:
            self._key.delete()
            self.settings = CentralSettings()
            self._path.unlink(missing_ok=True)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/test_central_state.py tests -k "keystore or central_state or security or phase2"`
Expected: all pass (existing keystore/security tests untouched).

- [ ] **Step 6: Commit**

```bash
git add data/keystore.py core/central_state.py tests/test_central_state.py
git commit -m "feat(central-sync): protected secret storage and central state"
```

---

### Task 2: Local project bookkeeping and `ProfileManager.adopt`

**Files:**
- Modify: `data/db.py` (schema + methods), `core/profile_manager.py`
- Test: `tests/test_central_projects.py`

**Interfaces:**
- Consumes: existing `Database.insert_project/update_project/get_project`, `ProfileManager.validate/save/_write/_path`.
- Produces: table `central_projects(project_id PK, central_version INT, managed INT, applied_at TEXT)`; `Database.set_central_project(project_id, central_version, managed=True)` (upsert), `get_central_project(project_id) -> Row | None`, `list_central_projects(managed_only=False) -> list[Row]`, `unmanage_central_project(project_id)`, `unmanage_all_central_projects()`; `ProfileManager.adopt(config: dict) -> dict` (creates the project under its own id at version 1, or saves a new version if it exists; raises `ProjectConfigError` for invalid configs and unsafe ids).

- [ ] **Step 1: Write the failing tests**

`tests/test_central_projects.py`:
```python
import copy
import json

import pytest

from core.profile_manager import (DEFAULT_PROJECT, ProfileManager,
                                  ProjectConfigError)
from data.db import Database


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "c.db")
    yield d
    d.close()


@pytest.fixture()
def pm(tmp_path, db):
    return ProfileManager(tmp_path / "projects", db)


def _cfg(project_id="11111111-2222-3333-4444-555555555555", name="Invoices"):
    cfg = copy.deepcopy(DEFAULT_PROJECT)
    cfg.update(project_id=project_id, name=name, version=7,
               created="2026-10-09T00:00:00+00:00")
    cfg["rules"]["rule_set_id"] = "rs-1"
    return cfg


# ---- db bookkeeping -----------------------------------------------------------
def test_central_project_upsert_get_list(db):
    assert db.get_central_project("p1") is None
    db.set_central_project("p1", 3)
    db.set_central_project("p2", 1)
    row = db.get_central_project("p1")
    assert row["central_version"] == 3 and row["managed"] == 1 and row["applied_at"]
    db.set_central_project("p1", 4)
    assert db.get_central_project("p1")["central_version"] == 4
    assert {r["project_id"] for r in db.list_central_projects()} == {"p1", "p2"}


def test_unmanage_one_and_all(db):
    db.set_central_project("p1", 1)
    db.set_central_project("p2", 1)
    db.unmanage_central_project("p1")
    assert db.get_central_project("p1")["managed"] == 0
    assert [r["project_id"] for r in db.list_central_projects(managed_only=True)] == ["p2"]
    db.unmanage_all_central_projects()
    assert db.list_central_projects(managed_only=True) == []
    assert len(db.list_central_projects()) == 2  # rows kept, just unmanaged


# ---- adopt ---------------------------------------------------------------------
def test_adopt_new_project_keeps_id_starts_at_version_1(pm, db, tmp_path):
    out = pm.adopt(_cfg())
    assert out["project_id"] == "11111111-2222-3333-4444-555555555555"
    assert out["version"] == 1
    assert (tmp_path / "projects" / "11111111-2222-3333-4444-555555555555.json").exists()
    row = db.get_project(out["project_id"])
    assert row["name"] == "Invoices" and row["version"] == 1


def test_adopt_existing_project_bumps_version_and_archives(pm, db, tmp_path):
    pm.adopt(_cfg())
    changed = _cfg()
    changed["ocr"]["lang"] = "deu"
    out = pm.adopt(changed)
    assert out["version"] == 2 and out["ocr"]["lang"] == "deu"
    assert pm.list_versions(out["project_id"]) == [1]
    assert db.get_project(out["project_id"])["version"] == 2


def test_adopt_when_db_row_exists_but_file_missing(pm, db, tmp_path):
    db.insert_project("aaaa-bbbb", "Old", _cfg("aaaa-bbbb", "Old"), version=5)
    out = pm.adopt(_cfg("aaaa-bbbb", "New"))
    assert out["version"] == 1
    assert db.get_project("aaaa-bbbb")["name"] == "New"


@pytest.mark.parametrize("bad_id", ["../evil", "a/b", "a\\b", "", "x" * 37,
                                    "has space", "..", "a.b"])
def test_adopt_rejects_unsafe_ids_and_writes_nothing(pm, tmp_path, bad_id):
    with pytest.raises(ProjectConfigError):
        pm.adopt(_cfg(project_id=bad_id))
    assert list((tmp_path).rglob("evil*")) == []
    assert list((tmp_path / "projects").glob("*.json")) == []


def test_adopt_rejects_invalid_config(pm):
    bad = _cfg()
    del bad["scanner"]
    with pytest.raises(ProjectConfigError):
        pm.adopt(bad)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/test_central_projects.py`
Expected: FAIL — `AttributeError: 'Database' object has no attribute 'set_central_project'`.

- [ ] **Step 3: Implement**

In `data/db.py` `_SCHEMA` add (next to the other `CREATE TABLE IF NOT EXISTS`, so existing and SQLCipher databases get it on open):
```sql
CREATE TABLE IF NOT EXISTS central_projects (
    project_id      TEXT PRIMARY KEY,
    central_version INTEGER NOT NULL,
    managed         INTEGER NOT NULL DEFAULT 1,
    applied_at      TEXT NOT NULL
);
```
and methods in the `Database` class (after the project section):
```python
    # -- central sync (multi-PC) -------------------------------------------
    def set_central_project(self, project_id: str, central_version: int,
                            managed: bool = True) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO central_projects (project_id, central_version, "
                "managed, applied_at) VALUES (?,?,?,?) "
                "ON CONFLICT(project_id) DO UPDATE SET "
                "central_version=excluded.central_version, "
                "managed=excluded.managed, applied_at=excluded.applied_at",
                (project_id, central_version, 1 if managed else 0, _now()))

    def get_central_project(self, project_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM central_projects WHERE project_id=?",
                (project_id,)).fetchone()

    def list_central_projects(self, managed_only: bool = False
                              ) -> list[sqlite3.Row]:
        q = "SELECT * FROM central_projects"
        if managed_only:
            q += " WHERE managed=1"
        with self._lock:
            return self.conn.execute(q + " ORDER BY project_id").fetchall()

    def unmanage_central_project(self, project_id: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE central_projects SET managed=0 WHERE project_id=?",
                (project_id,))

    def unmanage_all_central_projects(self) -> None:
        with self._lock, self.conn:
            self.conn.execute("UPDATE central_projects SET managed=0")
```
In `core/profile_manager.py` add `import re`, the module constant `_SAFE_ID = re.compile(r"[A-Za-z0-9-]{1,36}")`, and the method (place after `save`):
```python
    def adopt(self, config: dict) -> dict:
        """Install a config that arrived from elsewhere (central) under its
        own project_id: create it at version 1, or save a new version if the
        project already exists. The id becomes a file name, so it must be
        safe; the incoming 'version' is ignored (local counter)."""
        self.validate(config)
        project_id = config["project_id"]
        if not isinstance(project_id, str) or not _SAFE_ID.fullmatch(project_id):
            raise ProjectConfigError(f"unsafe project_id: {project_id!r}")
        if self._path(project_id).exists():
            return self.save(config)
        cfg = {**config, "version": 1}
        self._write(cfg)
        if self.db:
            if self.db.get_project(project_id) is None:
                self.db.insert_project(project_id, cfg["name"], cfg)
            else:
                self.db.update_project(project_id, cfg["name"], cfg, 1,
                                       cfg.get("status", "active"))
        return cfg
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/test_central_projects.py tests/test_profile_manager.py tests/test_db.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add data/db.py core/profile_manager.py tests/test_central_projects.py
git commit -m "feat(central-sync): central_projects bookkeeping and ProfileManager.adopt"
```

---

### Task 3: HTTPS client for the central API

**Files:**
- Create: `core/central_client.py`
- Test: `tests/test_central_client.py`

**Interfaces:**
- Produces: exceptions `CentralError(Exception)`, `CentralUnavailable(CentralError)`, `CentralAuthError(CentralError)` with attribute `.status: int`, `CentralRegistrationError(CentralError)`; `Transport = Callable[[str, str, dict, bytes | None, float], tuple[int, bytes]]` (method, url, headers, body, timeout → status, body bytes; raises `CentralUnavailable` on network errors); `urllib_transport`; `MAX_RESPONSE_BYTES = 5 * 1024 * 1024`; `allow_loopback_http() -> bool` (env switch); `@dataclass RegisterResult(pc_id, api_key, poll_interval_seconds)`; `@dataclass PullResult(pc_id, poll_interval_seconds, projects: list[dict])`; `CentralClient(server_url, transport=urllib_transport, timeout=15.0, allow_insecure_loopback=False)` with `.register(code) -> RegisterResult` and `.pull_config(pc_id, api_key, operator_id="") -> PullResult`. `ValueError` for a bad server URL.

- [ ] **Step 1: Write the failing tests**

`tests/test_central_client.py`:
```python
import http.server
import json
import threading

import pytest

from core.central_client import (MAX_RESPONSE_BYTES, CentralAuthError,
                                 CentralClient, CentralRegistrationError,
                                 CentralUnavailable, urllib_transport)


class FakeTransport:
    def __init__(self, status=200, payload=None, raw=None, exc=None):
        self.status, self.payload, self.raw, self.exc = status, payload, raw, exc
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body, timeout))
        if self.exc:
            raise self.exc
        data = self.raw if self.raw is not None else json.dumps(self.payload).encode()
        return self.status, data


PULL_OK = {"pc_id": "pc-1", "poll_interval_seconds": 300,
           "projects": [{"project_id": "p1", "name": "Inv", "version": 2,
                         "config": {"a": 1}}]}


def client(t, **kw):
    return CentralClient("https://central.example", transport=t, **kw)


# ---- URL policy ---------------------------------------------------------------
@pytest.mark.parametrize("url", ["http://central.example", "ftp://x", "central.example",
                                 "", "https://", "http://127.0.0.1.evil.com"])
def test_rejects_non_https_urls(url):
    with pytest.raises(ValueError):
        CentralClient(url, transport=FakeTransport())


def test_http_allowed_only_for_loopback_with_the_flag():
    with pytest.raises(ValueError):
        CentralClient("http://127.0.0.1:8000", transport=FakeTransport())
    CentralClient("http://127.0.0.1:8000", transport=FakeTransport(),
                  allow_insecure_loopback=True)
    CentralClient("http://localhost:8000", transport=FakeTransport(),
                  allow_insecure_loopback=True)
    with pytest.raises(ValueError):
        CentralClient("http://example.com", transport=FakeTransport(),
                      allow_insecure_loopback=True)


# ---- register -----------------------------------------------------------------
def test_register_success():
    t = FakeTransport(payload={"pc_id": "pc-1", "api_key": "dsk_x",
                               "poll_interval_seconds": 600})
    r = client(t).register("ABCDEFGH1234")
    assert (r.pc_id, r.api_key, r.poll_interval_seconds) == ("pc-1", "dsk_x", 600)
    method, url, headers, body, _ = t.calls[0]
    assert method == "POST" and url == "https://central.example/pcs/register"
    assert json.loads(body) == {"code": "ABCDEFGH1234"}
    assert headers["Content-Type"] == "application/json"


def test_register_bad_code_is_registration_error():
    with pytest.raises(CentralRegistrationError):
        client(FakeTransport(status=400, payload={"detail": "x"})).register("c" * 12)


def test_register_server_error_is_unavailable():
    with pytest.raises(CentralUnavailable):
        client(FakeTransport(status=503, raw=b"oops")).register("c" * 12)


def test_register_malformed_response_is_unavailable():
    with pytest.raises(CentralUnavailable):
        client(FakeTransport(payload={"pc_id": "x"})).register("c" * 12)


# ---- pull ---------------------------------------------------------------------
def test_pull_success_sends_bearer_and_encoded_operator():
    t = FakeTransport(payload=PULL_OK)
    r = client(t).pull_config("pc-1", "dsk_key", "Zoë/x y")
    assert r.pc_id == "pc-1" and r.poll_interval_seconds == 300
    assert r.projects[0]["version"] == 2
    method, url, headers, body, _ = t.calls[0]
    assert method == "GET" and url == "https://central.example/pcs/pc-1/config"
    assert headers["Authorization"] == "Bearer dsk_key"
    assert headers["X-Operator-Id"] == "Zo%C3%AB%2Fx%20y"
    assert body is None


def test_pull_without_operator_sends_no_header():
    t = FakeTransport(payload=PULL_OK)
    client(t).pull_config("pc-1", "k")
    assert "X-Operator-Id" not in t.calls[0][2]


def test_pc_id_is_url_quoted_in_the_path():
    t = FakeTransport(payload=PULL_OK)
    client(t).pull_config("a/b c", "k")
    assert t.calls[0][1] == "https://central.example/pcs/a%2Fb%20c/config"


@pytest.mark.parametrize("status", [401, 403])
def test_pull_auth_failures(status):
    with pytest.raises(CentralAuthError) as e:
        client(FakeTransport(status=status, payload={"detail": "x"})).pull_config("pc-1", "k")
    assert e.value.status == status


@pytest.mark.parametrize("status", [500, 502, 404, 302, 429])
def test_pull_other_statuses_are_unavailable(status):
    with pytest.raises(CentralUnavailable):
        client(FakeTransport(status=status, raw=b"x")).pull_config("pc-1", "k")


def test_transport_failure_propagates_as_unavailable():
    with pytest.raises(CentralUnavailable):
        client(FakeTransport(exc=CentralUnavailable("dns"))).pull_config("pc-1", "k")


@pytest.mark.parametrize("raw", [b"not json", b"[]", b"null", b'"str"'])
def test_pull_non_object_json_is_unavailable(raw):
    with pytest.raises(CentralUnavailable):
        client(FakeTransport(raw=raw)).pull_config("pc-1", "k")


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("projects"),
    lambda d: d.update(projects="x"),
    lambda d: d.update(projects=["notadict"]),
    lambda d: d.update(poll_interval_seconds="x"),
    lambda d: d.pop("pc_id"),
])
def test_pull_malformed_structure_is_unavailable(mutate):
    d = json.loads(json.dumps(PULL_OK))
    mutate(d)
    with pytest.raises(CentralUnavailable):
        client(FakeTransport(payload=d)).pull_config("pc-1", "k")


def test_pull_poll_interval_is_clamped():
    d = dict(PULL_OK, poll_interval_seconds=1)
    assert client(FakeTransport(payload=d)).pull_config("pc-1", "k").poll_interval_seconds == 30


def test_oversized_response_is_rejected():
    big = b"x" * (MAX_RESPONSE_BYTES + 1)
    with pytest.raises(CentralUnavailable):
        client(FakeTransport(raw=big)).pull_config("pc-1", "k")


def test_per_project_entries_are_not_validated_here():
    # entry-level validation (ids, versions) belongs to config_overlay so one
    # bad project cannot hide the others
    d = dict(PULL_OK, projects=[{"project_id": "../x"}, PULL_OK["projects"][0]])
    r = client(FakeTransport(payload=d)).pull_config("pc-1", "k")
    assert len(r.projects) == 2


# ---- the real urllib transport against a loopback server ----------------------
class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/elsewhere")
            self.end_headers()
            return
        body = json.dumps(PULL_OK).encode()
        self.send_response(200 if self.path.endswith("/config") else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def loopback_server():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def test_urllib_transport_end_to_end(loopback_server):
    c = CentralClient(loopback_server, allow_insecure_loopback=True, timeout=5)
    assert c.pull_config("pc-1", "k").projects[0]["project_id"] == "p1"


def test_urllib_transport_does_not_follow_redirects(loopback_server):
    status, _ = urllib_transport("GET", loopback_server + "/redirect", {}, None, 5)
    assert status == 302


def test_urllib_transport_connection_refused_is_unavailable():
    with pytest.raises(CentralUnavailable):
        urllib_transport("GET", "http://127.0.0.1:1/x", {}, None, 2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/test_central_client.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.central_client'`.

- [ ] **Step 3: Implement `core/central_client.py`**

```python
"""HTTPS client for dokscanner-central (spec: sync client).

stdlib only. TLS is always verified, redirects are never followed, response
bodies are size-capped, and every failure maps to one of three exceptions so
the sync layer can decide what to do (keep working / stop polling / report).
"""
from __future__ import annotations

import http.client
import json
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote, urlparse

MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MIN_POLL, MAX_POLL = 30, 86400
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
LOOPBACK_ENV = "DOKSCANNER_CENTRAL_ALLOW_LOOPBACK_HTTP"

Transport = Callable[[str, str, dict, "bytes | None", float],
                     "tuple[int, bytes]"]


class CentralError(Exception):
    """Base class for central-service failures."""


class CentralUnavailable(CentralError):
    """Network error, timeout, 5xx/unexpected status, or a malformed reply."""


class CentralAuthError(CentralError):
    """401/403 on a PC call: the key is revoked, replaced, or not this PC's."""

    def __init__(self, status: int):
        super().__init__(f"central rejected this PC's key (HTTP {status})")
        self.status = status


class CentralRegistrationError(CentralError):
    """Enrollment code was invalid, used or expired."""


def allow_loopback_http() -> bool:
    return os.environ.get(LOOPBACK_ENV) == "1"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # surface the 3xx as an HTTPError instead of following


def urllib_transport(method: str, url: str, headers: dict,
                     body: bytes | None, timeout: float) -> tuple[int, bytes]:
    ctx = ssl.create_default_context()
    opener = urllib.request.build_opener(
        _NoRedirect, urllib.request.HTTPSHandler(context=ctx))
    req = urllib.request.Request(url, data=body, method=method,
                                 headers=dict(headers))
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read(MAX_RESPONSE_BYTES + 1)
        finally:
            e.close()
    except (urllib.error.URLError, OSError, ssl.SSLError,
            http.client.HTTPException) as e:
        raise CentralUnavailable(f"{type(e).__name__}: {e}") from e


@dataclass
class RegisterResult:
    pc_id: str
    api_key: str
    poll_interval_seconds: int


@dataclass
class PullResult:
    pc_id: str
    poll_interval_seconds: int
    projects: list[dict]


def _clamp(v: int) -> int:
    return max(MIN_POLL, min(MAX_POLL, v))


def _json_object(status_ok_body: bytes) -> dict:
    if len(status_ok_body) > MAX_RESPONSE_BYTES:
        raise CentralUnavailable("response too large")
    try:
        data = json.loads(status_ok_body)
    except ValueError as e:
        raise CentralUnavailable("response is not valid JSON") from e
    if not isinstance(data, dict):
        raise CentralUnavailable("response is not a JSON object")
    return data


class CentralClient:
    def __init__(self, server_url: str, transport: Transport = urllib_transport,
                 timeout: float = 15.0, allow_insecure_loopback: bool = False):
        url = (server_url or "").strip().rstrip("/")
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            raise ValueError("central server URL must be https://host[:port]")
        https_ok = parsed.scheme == "https"
        loopback_ok = (allow_insecure_loopback and parsed.scheme == "http"
                       and host in _LOOPBACK_HOSTS)
        if not (https_ok or loopback_ok):
            raise ValueError("central server URL must use https://")
        self.base = url
        self._transport = transport
        self._timeout = timeout

    def register(self, code: str) -> RegisterResult:
        body = json.dumps({"code": code}).encode("utf-8")
        status, data = self._transport(
            "POST", f"{self.base}/pcs/register",
            {"Content-Type": "application/json", "Accept": "application/json"},
            body, self._timeout)
        if status == 400:
            raise CentralRegistrationError(
                "invalid, used or expired enrollment code")
        if status != 200:
            raise CentralUnavailable(f"register failed (HTTP {status})")
        d = _json_object(data)
        pc_id, key, poll = d.get("pc_id"), d.get("api_key"), d.get(
            "poll_interval_seconds")
        if not (isinstance(pc_id, str) and pc_id and isinstance(key, str)
                and key and isinstance(poll, int) and not isinstance(poll, bool)):
            raise CentralUnavailable("malformed register response")
        return RegisterResult(pc_id, key, _clamp(poll))

    def pull_config(self, pc_id: str, api_key: str,
                    operator_id: str = "") -> PullResult:
        headers = {"Authorization": f"Bearer {api_key}",
                   "Accept": "application/json"}
        if operator_id:
            headers["X-Operator-Id"] = quote(operator_id, safe="")
        status, data = self._transport(
            "GET", f"{self.base}/pcs/{quote(pc_id, safe='')}/config",
            headers, None, self._timeout)
        if status in (401, 403):
            raise CentralAuthError(status)
        if status != 200:
            raise CentralUnavailable(f"pull failed (HTTP {status})")
        d = _json_object(data)
        pid, poll, projects = d.get("pc_id"), d.get("poll_interval_seconds"), \
            d.get("projects")
        if not (isinstance(pid, str) and isinstance(poll, int)
                and not isinstance(poll, bool) and isinstance(projects, list)
                and all(isinstance(p, dict) for p in projects)):
            raise CentralUnavailable("malformed config response")
        return PullResult(pid, _clamp(poll), projects)
```
Note `_json_object` also receives oversize bodies from the transport (the transport reads `MAX_RESPONSE_BYTES + 1` bytes), which is why the check lives there.

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/test_central_client.py`
Expected: all pass. (If `test_rejects_non_https_urls` fails for `http://127.0.0.1.evil.com`, the loopback check must compare the exact parsed hostname, as written.)

- [ ] **Step 5: Commit**

```bash
git add core/central_client.py tests/test_central_client.py
git commit -m "feat(central-sync): stdlib HTTPS client for the central API"
```

---

### Task 4: Pulled-entry validation and the local-sections overlay

**Files:**
- Create: `core/config_overlay.py`
- Test: `tests/test_config_overlay.py`

**Interfaces:**
- Consumes: `core.profile_manager.DEFAULT_PROJECT`.
- Produces: `LOCAL_ONLY_KEYS = ("scanner", "export", "delivery")`; `class PulledProjectError(ValueError)`; `validate_entry(entry) -> None` (raises `PulledProjectError`); `default_local_sections(root: Path) -> dict` (`{"scanner":..., "export":..., "delivery":...}` seeded with `root/output` and `root/delivered`, same as `main.build_default_project`); `overlay(entry: dict, local: dict | None, defaults: dict) -> dict` (a full project config ready for `ProfileManager.adopt`).

- [ ] **Step 1: Write the failing tests**

`tests/test_config_overlay.py`:
```python
import copy
from pathlib import Path

import pytest

from core.config_overlay import (LOCAL_ONLY_KEYS, PulledProjectError,
                                 default_local_sections, overlay,
                                 validate_entry)
from core.profile_manager import DEFAULT_PROJECT

PID = "11111111-2222-3333-4444-555555555555"


def entry(**over):
    cfg = copy.deepcopy(DEFAULT_PROJECT)
    cfg.update(project_id=PID, name="Invoices")
    cfg["ocr"]["lang"] = "deu"
    e = {"project_id": PID, "name": "Invoices", "version": 3, "config": cfg}
    e.update(over)
    return e


# ---- validate_entry -----------------------------------------------------------
def test_valid_entry_passes():
    validate_entry(entry())


@pytest.mark.parametrize("bad_id", ["../evil", "a/b", "a\\b", "", "x" * 37,
                                    "has space", "..", "a.b", 5, None])
def test_unsafe_ids_rejected(bad_id):
    with pytest.raises(PulledProjectError):
        validate_entry(entry(project_id=bad_id))


@pytest.mark.parametrize("bad_version", [0, -1, "2", 1.5, True, None])
def test_bad_versions_rejected(bad_version):
    with pytest.raises(PulledProjectError):
        validate_entry(entry(version=bad_version))


@pytest.mark.parametrize("field,value", [("name", ""), ("name", 5),
                                         ("config", "x"), ("config", None)])
def test_bad_name_or_config_rejected(field, value):
    with pytest.raises(PulledProjectError):
        validate_entry(entry(**{field: value}))


def test_config_project_id_must_match_entry_id():
    e = entry()
    e["config"]["project_id"] = "someone-else"
    with pytest.raises(PulledProjectError):
        validate_entry(e)


def test_non_dict_entry_rejected():
    with pytest.raises(PulledProjectError):
        validate_entry("nope")


# ---- default_local_sections ----------------------------------------------------
def test_default_local_sections_are_seeded_with_local_paths(tmp_path):
    d = default_local_sections(tmp_path)
    assert set(d) == set(LOCAL_ONLY_KEYS)
    assert d["export"]["output_path"] == str(tmp_path / "output")
    assert d["delivery"]["local_folder"]["path"] == str(tmp_path / "delivered")
    assert d["delivery"]["sftp"].get("enabled") is False
    d2 = default_local_sections(tmp_path)
    d["export"]["output_path"] = "mutated"
    assert d2["export"]["output_path"] != "mutated"  # fresh copy each call


# ---- overlay ---------------------------------------------------------------------
def test_first_pull_seeds_local_sections(tmp_path):
    defaults = default_local_sections(tmp_path)
    cfg = overlay(entry(), None, defaults)
    assert cfg["project_id"] == PID and cfg["name"] == "Invoices"
    assert cfg["ocr"]["lang"] == "deu"                    # central content applied
    assert cfg["export"]["output_path"] == str(tmp_path / "output")
    assert cfg["delivery"] == defaults["delivery"]


def test_update_keeps_local_sections_untouched(tmp_path):
    local = copy.deepcopy(DEFAULT_PROJECT)
    local["scanner"] = {"dpi": 600, "color": "color", "duplex": False, "sdk": "twain"}
    local["export"]["output_path"] = "D:/scans/out"
    local["delivery"]["sftp"] = {"enabled": True, "host": "10.0.0.5",
                                 "username": "svc", "password": "local-secret"}
    cfg = overlay(entry(), local, default_local_sections(tmp_path))
    assert cfg["scanner"] == local["scanner"]
    assert cfg["export"] == local["export"]
    assert cfg["delivery"] == local["delivery"]
    assert cfg["ocr"]["lang"] == "deu"


def test_central_can_never_supply_local_sections(tmp_path):
    e = entry()
    e["config"]["delivery"]["sftp"] = {"enabled": True, "host": "evil.example"}
    e["config"]["export"]["output_path"] = "\\\\evil\\share"
    e["config"]["scanner"] = {"dpi": 1, "color": "x", "duplex": True, "sdk": "evil"}
    cfg = overlay(e, None, default_local_sections(tmp_path))
    assert cfg["delivery"]["sftp"].get("host") is None
    assert "evil" not in cfg["export"]["output_path"]
    assert cfg["scanner"]["sdk"] != "evil"


def test_overlay_does_not_mutate_inputs(tmp_path):
    e, local = entry(), copy.deepcopy(DEFAULT_PROJECT)
    e0, l0 = copy.deepcopy(e), copy.deepcopy(local)
    overlay(e, local, default_local_sections(tmp_path))
    assert e == e0 and local == l0


def test_entry_name_and_id_win_over_config_values(tmp_path):
    e = entry()
    e["config"]["name"] = "stale"
    cfg = overlay(e, None, default_local_sections(tmp_path))
    assert cfg["name"] == "Invoices" and cfg["project_id"] == PID
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/test_config_overlay.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.config_overlay'`.

- [ ] **Step 3: Implement `core/config_overlay.py`**

```python
"""Merge a project pulled from central with this PC's local-only sections.

Mirrors _LIVE_ONLY_TOP_KEYS in core/template_manager.py: central owns the
processing settings; the PC owns where it scans from and where output goes.
Nothing under LOCAL_ONLY_KEYS is ever taken from a pulled config.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

from core.profile_manager import DEFAULT_PROJECT

LOCAL_ONLY_KEYS = ("scanner", "export", "delivery")
_SAFE_ID = re.compile(r"[A-Za-z0-9-]{1,36}")


class PulledProjectError(ValueError):
    """A pulled project entry is unusable; the others may still apply."""


def validate_entry(entry) -> None:
    if not isinstance(entry, dict):
        raise PulledProjectError("project entry is not an object")
    pid = entry.get("project_id")
    if not isinstance(pid, str) or not _SAFE_ID.fullmatch(pid):
        raise PulledProjectError(f"unsafe project_id: {pid!r}")
    version = entry.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise PulledProjectError(f"invalid version: {version!r}")
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PulledProjectError("missing project name")
    config = entry.get("config")
    if not isinstance(config, dict):
        raise PulledProjectError("config is not an object")
    if config.get("project_id") not in (None, pid):
        raise PulledProjectError("config.project_id does not match the entry")


def default_local_sections(root: Path) -> dict:
    """Local defaults for a project this PC has never had: same paths as
    main.build_default_project uses for the first-run project."""
    root = Path(root)
    base = copy.deepcopy(DEFAULT_PROJECT)
    sections = {k: base[k] for k in LOCAL_ONLY_KEYS}
    sections["export"]["output_path"] = str(root / "output")
    sections["delivery"]["local_folder"]["path"] = str(root / "delivered")
    return sections


def overlay(entry: dict, local: dict | None, defaults: dict) -> dict:
    cfg = copy.deepcopy(entry["config"])
    cfg["project_id"] = entry["project_id"]
    cfg["name"] = entry["name"]
    source = local if local is not None else defaults
    for key in LOCAL_ONLY_KEYS:
        cfg[key] = copy.deepcopy(source[key])
    return cfg
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/test_config_overlay.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add core/config_overlay.py tests/test_config_overlay.py
git commit -m "feat(central-sync): entry validation and local-sections overlay"
```

---

### Task 5: Sync service (apply, unassign, failure handling, register, disconnect)

**Files:**
- Create: `core/central_sync.py` (service half; the worker and profile chooser are added in Task 6)
- Test: `tests/test_central_sync.py`

**Interfaces:**
- Consumes: `CentralState`, `CentralClient` + errors, `ProfileManager.adopt/…`, `Database.*central*`, `config_overlay.*`, `AuditLogger.log(action, user, document_id=None, **detail)` (optional).
- Produces:
  - `@dataclass SyncResult(status: str, applied: list[str] = [], unchanged: int = 0, unassigned: list[str] = [], errors: dict[str, str] = {}, message: str = "")` with `status ∈ {"not_registered","ok","auth_failed","unavailable"}` and property `restart_needed -> bool` (`bool(applied)`).
  - `CentralSyncService(db, pm, state, root, audit=None, client_factory=default_client_factory, operator_fn=lambda: "", timeout=15.0)` with `sync_once() -> SyncResult`, `register(server_url: str, code: str) -> None`, `disconnect() -> None`.
  - `default_client_factory(server_url, timeout) -> CentralClient` (uses `allow_loopback_http()`).
  - Audit actor string: `"central-sync"`; actions `central_registered`, `central_project_applied`, `central_project_skipped`, `central_project_unassigned`, `central_auth_failed`, `central_disconnected`.

- [ ] **Step 1: Write the failing tests**

`tests/test_central_sync.py`:
```python
import copy
import json

import pytest

from core.central_client import (CentralAuthError, CentralRegistrationError,
                                 CentralUnavailable, PullResult, RegisterResult)
from core.central_state import CentralState
from core.central_sync import CentralSyncService, SyncResult
from core.config_overlay import default_local_sections
from core.profile_manager import DEFAULT_PROJECT, ProfileManager
from data.audit import AuditLogger
from data.db import Database

PID = "11111111-2222-3333-4444-555555555555"
PID2 = "99999999-2222-3333-4444-555555555555"


def project_entry(pid=PID, name="Invoices", version=1, lang="eng", **cfg_over):
    cfg = copy.deepcopy(DEFAULT_PROJECT)
    cfg.update(project_id=pid, name=name, **cfg_over)
    cfg["ocr"]["lang"] = lang
    cfg["rules"]["rule_set_id"] = "rs"
    return {"project_id": pid, "name": name, "version": version, "config": cfg}


class FakeClient:
    def __init__(self, pull=None, exc=None, register=None):
        self.pull, self.exc, self._register = pull, exc, register
        self.pulls = []
        self.registered = []

    def pull_config(self, pc_id, api_key, operator_id=""):
        self.pulls.append((pc_id, api_key, operator_id))
        if self.exc:
            raise self.exc
        return self.pull

    def register(self, code):
        self.registered.append(code)
        if isinstance(self._register, Exception):
            raise self._register
        return self._register


@pytest.fixture()
def env(tmp_path):
    db = Database(tmp_path / "c.db")
    pm = ProfileManager(tmp_path / "projects", db)
    state = CentralState(tmp_path)
    audit = AuditLogger(db, tmp_path / "logs")
    holder = {"client": FakeClient(pull=PullResult("pc-1", 600, []))}
    created = []

    def factory(url, timeout):
        created.append((url, timeout))
        return holder["client"]

    svc = CentralSyncService(db, pm, state, tmp_path, audit=audit,
                             client_factory=factory,
                             operator_fn=lambda: "alice")
    yield type("Env", (), dict(db=db, pm=pm, state=state, svc=svc, audit=audit,
                               holder=holder, created=created, root=tmp_path))
    audit.close()
    db.close()


def register(env):
    env.state.register_result("https://c.example", "pc-1", "dsk_KEY", 600)


def pull(env, *entries, poll=600):
    env.holder["client"] = FakeClient(pull=PullResult("pc-1", poll, list(entries)))


def actions(env):
    return [r["action"] for r in env.db.conn.execute(
        "SELECT action FROM audit_log ORDER BY log_id")]


# ---- not registered ---------------------------------------------------------------
def test_unregistered_pc_makes_no_network_call(env):
    r = env.svc.sync_once()
    assert r.status == "not_registered" and not r.applied
    assert env.created == [] and env.holder["client"].pulls == []


# ---- first pull / updates ------------------------------------------------------------
def test_first_pull_creates_project_with_seeded_local_sections(env):
    register(env)
    pull(env, project_entry(lang="deu"))
    r = env.svc.sync_once()
    assert r.status == "ok" and r.applied == [PID] and r.restart_needed
    cfg = env.pm.load(PID)
    assert cfg["ocr"]["lang"] == "deu" and cfg["version"] == 1
    assert cfg["export"]["output_path"] == str(env.root / "output")
    rec = env.db.get_central_project(PID)
    assert rec["central_version"] == 1 and rec["managed"] == 1
    assert env.holder["client"].pulls == [("pc-1", "dsk_KEY", "alice")]
    assert env.created == [("https://c.example", 15.0)]
    assert "central_project_applied" in actions(env)


def test_same_version_again_is_a_noop(env):
    register(env)
    pull(env, project_entry())
    env.svc.sync_once()
    pull(env, project_entry())
    r = env.svc.sync_once()
    assert r.applied == [] and r.unchanged == 1 and not r.restart_needed
    assert env.pm.load(PID)["version"] == 1          # no spurious local version bump


def test_new_central_version_updates_and_preserves_local_sections(env):
    register(env)
    pull(env, project_entry(lang="eng"))
    env.svc.sync_once()
    local = env.pm.load(PID)
    local["export"]["output_path"] = "D:/my/output"
    local["delivery"]["sftp"] = {"enabled": True, "host": "10.1.1.1",
                                 "username": "u", "password": "pw"}
    env.pm.save(local)                               # local edits to local-only sections
    pull(env, project_entry(version=2, lang="fra"))
    r = env.svc.sync_once()
    assert r.applied == [PID]
    cfg = env.pm.load(PID)
    assert cfg["ocr"]["lang"] == "fra"
    assert cfg["export"]["output_path"] == "D:/my/output"
    assert cfg["delivery"]["sftp"]["password"] == "pw"
    assert env.db.get_central_project(PID)["central_version"] == 2


def test_adopts_existing_local_project_with_same_id_keeping_local_sections(env):
    register(env)
    local = copy.deepcopy(DEFAULT_PROJECT)
    local.update(project_id=PID, name="Old", version=4,
                 created="2026-01-01T00:00:00+00:00")
    local["rules"]["rule_set_id"] = "r"
    local["export"]["output_path"] = "E:/keep"
    env.pm.adopt(local)
    pull(env, project_entry(name="Invoices", version=1))
    env.svc.sync_once()
    cfg = env.pm.load(PID)
    assert cfg["name"] == "Invoices" and cfg["export"]["output_path"] == "E:/keep"


def test_poll_interval_and_status_recorded(env):
    register(env)
    pull(env, poll=90)
    r = env.svc.sync_once()
    s = env.state.settings
    assert r.status == "ok" and s.poll_interval_seconds == 90
    assert s.last_sync_status == "ok"
    assert s.last_sync_at.endswith("Z")


# ---- unassignment -----------------------------------------------------------------------
def test_unassigned_project_is_kept_but_unmanaged(env):
    register(env)
    pull(env, project_entry(), project_entry(pid=PID2, name="Other"))
    env.svc.sync_once()
    pull(env, project_entry(version=1, pid=PID2, name="Other"))  # PID disappears
    r = env.svc.sync_once()
    assert r.unassigned == [PID]
    assert env.db.get_project(PID) is not None and env.pm.load(PID)
    assert env.db.get_central_project(PID)["managed"] == 0
    assert env.db.get_central_project(PID2)["managed"] == 1
    assert "central_project_unassigned" in actions(env)


def test_reassigned_project_becomes_managed_again(env):
    register(env)
    pull(env, project_entry())
    env.svc.sync_once()
    pull(env)
    env.svc.sync_once()
    pull(env, project_entry(version=1))
    r = env.svc.sync_once()
    assert r.applied == [PID] and env.db.get_central_project(PID)["managed"] == 1


# ---- bad entries ----------------------------------------------------------------------------
def test_path_traversal_id_is_skipped_nothing_written_outside(env):
    register(env)
    evil = project_entry(pid="../../evil", name="Evil")
    pull(env, evil, project_entry(pid=PID2, name="Good"))
    r = env.svc.sync_once()
    assert r.status == "ok" and r.applied == [PID2]
    assert any("evil" in k for k in r.errors)
    assert list(env.root.rglob("evil*")) == []
    assert "central_project_skipped" in actions(env)


def test_schema_invalid_project_is_skipped_others_apply_and_old_version_stays(env):
    register(env)
    pull(env, project_entry(lang="eng"))
    env.svc.sync_once()
    broken = project_entry(version=2)
    del broken["config"]["qc"]                       # newer server, incompatible
    pull(env, broken, project_entry(pid=PID2, name="Good"))
    r = env.svc.sync_once()
    assert PID in r.errors and r.applied == [PID2]
    assert env.pm.load(PID)["version"] == 1          # previous local version intact
    assert env.db.get_central_project(PID)["central_version"] == 1
    assert env.db.get_central_project(PID)["managed"] == 1   # not "unassigned"
    assert "with 1 skipped" in env.state.settings.last_sync_status


def test_garbage_entry_does_not_crash_or_unassign(env):
    register(env)
    pull(env, project_entry())
    env.svc.sync_once()
    pull(env, "garbage", {"no": "id"}, project_entry(version=1))
    r = env.svc.sync_once()
    assert r.status == "ok" and r.unassigned == [] and r.unchanged == 1


# ---- failures --------------------------------------------------------------------------------
def test_unavailable_keeps_everything_and_reports(env):
    register(env)
    pull(env, project_entry())
    env.svc.sync_once()
    env.holder["client"] = FakeClient(exc=CentralUnavailable("dns failure"))
    r = env.svc.sync_once()
    assert r.status == "unavailable" and "dns failure" in r.message
    assert env.pm.load(PID)["version"] == 1
    assert env.db.get_central_project(PID)["managed"] == 1
    assert "unavailable" in env.state.settings.last_sync_status
    assert env.state.settings.auth_failed is False


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failure_is_persisted_and_stops_further_calls(env, status):
    register(env)
    env.holder["client"] = FakeClient(exc=CentralAuthError(status))
    r = env.svc.sync_once()
    assert r.status == "auth_failed"
    assert env.state.settings.auth_failed is True
    assert "central_auth_failed" in actions(env)
    client2 = FakeClient(pull=PullResult("pc-1", 600, []))
    env.holder["client"] = client2
    r2 = env.svc.sync_once()
    assert r2.status == "auth_failed" and client2.pulls == []     # no hammering
    assert CentralState(env.root).settings.auth_failed is True    # survives restart


def test_missing_api_key_is_treated_as_unregistered(env):
    register(env)
    (env.root / "keys" / "central_api_key.bin").unlink()
    assert env.svc.sync_once().status == "not_registered"


def test_unexpected_client_exception_is_reported_not_raised(env):
    register(env)
    env.holder["client"] = FakeClient(exc=RuntimeError("boom"))
    r = env.svc.sync_once()
    assert r.status == "unavailable" and "boom" in r.message


# ---- register / disconnect -----------------------------------------------------------------------
def test_register_success_stores_state_and_audits(env):
    env.holder["client"] = FakeClient(register=RegisterResult("pc-9", "dsk_NEW", 120))
    env.svc.register("https://c.example/", "ABCDEFGH1234")
    s = env.state
    assert s.is_registered() and s.api_key() == "dsk_NEW"
    assert s.settings.pc_id == "pc-9" and s.settings.poll_interval_seconds == 120
    assert s.settings.server_url == "https://c.example"
    assert "central_registered" in actions(env)
    # the key itself must never reach the audit log
    assert "dsk_NEW" not in json.dumps([dict(r) for r in env.db.conn.execute("SELECT * FROM audit_log")])


def test_register_clears_auth_failed(env):
    register(env)
    env.state.update(auth_failed=True)
    env.holder["client"] = FakeClient(register=RegisterResult("pc-1", "dsk_NEW2", 600))
    env.svc.register("https://c.example", "ABCDEFGH1234")
    assert env.state.settings.auth_failed is False and env.state.api_key() == "dsk_NEW2"


def test_register_failure_leaves_state_untouched(env):
    env.holder["client"] = FakeClient(register=CentralRegistrationError("bad"))
    with pytest.raises(CentralRegistrationError):
        env.svc.register("https://c.example", "ABCDEFGH1234")
    assert not env.state.is_registered()


def test_register_rejects_non_https_url_before_any_call(env):
    from core.central_sync import default_client_factory
    env.svc._client_factory = default_client_factory
    with pytest.raises(ValueError):
        env.svc.register("http://central.example", "ABCDEFGH1234")


def test_disconnect_unmanages_projects_but_keeps_them(env):
    register(env)
    pull(env, project_entry())
    env.svc.sync_once()
    env.svc.disconnect()
    assert not env.state.is_registered() and env.state.api_key() is None
    assert env.db.get_central_project(PID)["managed"] == 0
    assert env.pm.load(PID)                                      # local data kept
    assert "central_disconnected" in actions(env)
    assert env.svc.sync_once().status == "not_registered"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/test_central_sync.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.central_sync'`.

- [ ] **Step 3: Implement `core/central_sync.py` (service half)**

```python
"""Apply the projects central assigned to this PC (spec: sync client).

SyncService.sync_once() is safe to call from any thread and never raises:
every outcome is a SyncResult. It never touches the running pipeline — new
or changed projects are written to the local project store/database and take
effect for the pipeline at the next application start (restart_needed).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core.central_client import (CentralAuthError, CentralClient,
                                 CentralError, CentralRegistrationError,
                                 CentralUnavailable, allow_loopback_http)
from core.central_state import CentralState
from core.config_overlay import (PulledProjectError, default_local_sections,
                                 overlay, validate_entry)
from core.profile_manager import ProfileManager, ProjectConfigError

log = logging.getLogger(__name__)

ACTOR = "central-sync"


@dataclass
class SyncResult:
    status: str  # not_registered | ok | auth_failed | unavailable
    applied: list[str] = field(default_factory=list)
    unchanged: int = 0
    unassigned: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    message: str = ""

    @property
    def restart_needed(self) -> bool:
        return bool(self.applied)


def default_client_factory(server_url: str, timeout: float) -> CentralClient:
    return CentralClient(server_url, timeout=timeout,
                         allow_insecure_loopback=allow_loopback_http())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z")


class CentralSyncService:
    def __init__(self, db, pm: ProfileManager, state: CentralState,
                 root: str | Path, audit=None,
                 client_factory=default_client_factory,
                 operator_fn=lambda: "", timeout: float = 15.0):
        self.db, self.pm, self.state = db, pm, state
        self.root = Path(root)
        self.audit = audit
        self._client_factory = client_factory
        self._operator_fn = operator_fn
        self.timeout = timeout

    def _audit(self, action: str, **detail) -> None:
        if self.audit is not None:
            try:
                self.audit.log(action, ACTOR, **detail)
            except Exception:                       # audit must never break sync
                log.exception("audit failed for %s", action)

    # -- enrollment -----------------------------------------------------------
    def register(self, server_url: str, code: str) -> None:
        """Exchange a one-time enrollment code for this PC's identity.
        Raises ValueError (bad URL), CentralRegistrationError (bad code) or
        CentralUnavailable; leaves the local state untouched on failure."""
        url = server_url.strip().rstrip("/")
        client = self._client_factory(url, self.timeout)
        result = client.register(code.strip())
        self.state.register_result(url, result.pc_id, result.api_key,
                                   result.poll_interval_seconds)
        self._audit("central_registered", pc_id=result.pc_id, server=url)

    def disconnect(self) -> None:
        """Forget the registration; keep every local project and its data."""
        self.db.unmanage_all_central_projects()
        self.state.clear()
        self._audit("central_disconnected")

    # -- the sync itself ---------------------------------------------------------
    def sync_once(self) -> SyncResult:
        if not self.state.is_registered():
            return SyncResult("not_registered")
        s = self.state.settings
        if s.auth_failed:
            return SyncResult("auth_failed",
                              message="re-enrollment required")
        api_key = self.state.api_key()
        if not api_key:
            return SyncResult("not_registered")
        try:
            client = self._client_factory(s.server_url, self.timeout)
            pulled = client.pull_config(s.pc_id, api_key, self._operator_fn())
        except CentralAuthError as e:
            self.state.update(auth_failed=True, last_sync_at=_now(),
                              last_sync_status=f"auth failed (HTTP {e.status})")
            self._audit("central_auth_failed", status=e.status)
            log.warning("central rejected this PC's key (HTTP %s); polling "
                        "stopped until re-enrollment", e.status)
            return SyncResult("auth_failed", message=str(e))
        except (CentralError, ValueError, OSError) as e:
            return self._unavailable(str(e))
        except Exception as e:                      # never let sync crash a thread
            log.exception("unexpected central sync error")
            return self._unavailable(f"{type(e).__name__}: {e}")
        return self._apply(pulled)

    def _unavailable(self, message: str) -> SyncResult:
        log.warning("central unavailable: %s", message)
        self.state.update(last_sync_at=_now(),
                          last_sync_status=f"unavailable: {message}"[:200])
        return SyncResult("unavailable", message=message)

    def _apply(self, pulled) -> SyncResult:
        result = SyncResult("ok")
        managed_before = {r["project_id"] for r in
                          self.db.list_central_projects(managed_only=True)}
        seen: set[str] = set()
        defaults = default_local_sections(self.root)
        for entry in pulled.projects:
            raw_id = entry.get("project_id") if isinstance(entry, dict) else None
            if isinstance(raw_id, str):
                seen.add(raw_id)          # present on the server: not "unassigned"
            try:
                validate_entry(entry)
                self._apply_entry(entry, defaults, result)
            except (PulledProjectError, ProjectConfigError) as e:
                self._skip(result, raw_id, str(e))
            except Exception as e:        # one bad project must not stop the rest
                log.exception("applying central project %r failed", raw_id)
                self._skip(result, raw_id, f"{type(e).__name__}: {e}")
        for pid in sorted(managed_before - seen):
            self.db.unmanage_central_project(pid)
            result.unassigned.append(pid)
            self._audit("central_project_unassigned", project_id=pid)
        status = "ok" if not result.errors else \
            f"ok with {len(result.errors)} skipped"
        self.state.update(poll_interval_seconds=pulled.poll_interval_seconds,
                          last_sync_at=_now(), last_sync_status=status,
                          auth_failed=False)
        return result

    def _apply_entry(self, entry: dict, defaults: dict,
                     result: SyncResult) -> None:
        pid, version = entry["project_id"], entry["version"]
        rec = self.db.get_central_project(pid)
        if rec is not None and rec["managed"] and \
                rec["central_version"] == version:
            result.unchanged += 1
            return
        row = self.db.get_project(pid)
        local = json.loads(row["config_json"]) if row is not None else None
        cfg = overlay(entry, local, defaults)
        self.pm.adopt(cfg)
        self.db.set_central_project(pid, version, managed=True)
        result.applied.append(pid)
        self._audit("central_project_applied", project_id=pid,
                    central_version=version)

    def _skip(self, result: SyncResult, raw_id, reason: str) -> None:
        key = str(raw_id)
        result.errors[key] = reason
        self._audit("central_project_skipped", project_id=key, reason=reason)
        log.warning("skipped central project %s: %s", key, reason)
```
Remove unused imports (`CentralRegistrationError`, `CentralUnavailable`) if flake/bandit complains; keep `CentralError` (parent of the three).

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/test_central_sync.py tests/test_central_state.py tests/test_central_projects.py tests/test_central_client.py tests/test_config_overlay.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add core/central_sync.py tests/test_central_sync.py
git commit -m "feat(central-sync): sync service with overlay, unassignment and failure handling"
```

---

### Task 6: Background worker, event-bus signal, startup wiring, profile choice

**Files:**
- Modify: `core/central_sync.py` (add `SyncWorker`, `choose_pipeline_profile`), `core/event_bus.py`, `main.py`
- Test: `tests/test_central_worker.py`

**Interfaces:**
- Consumes: `CentralSyncService`, `CentralState`, `SyncResult`.
- Produces: `RETRY_AFTER_FAILURE = 120.0`; `SyncWorker(service, state, on_result=None)` with `start(initial_sync=True)`, `stop()`, `sync_now()`, `next_wait(result) -> float | None` (pure: `None` means "sleep until woken"); `EventBus.central_synced = pyqtSignal(object)` (a `SyncResult`); `choose_pipeline_profile(db, fallback) -> dict` (the first active **managed** central project's config, else `fallback()`); `main.py` runs one short-timeout `sync_once()` before choosing the pipeline profile, then starts the worker with `initial_sync=False`, and stops it on exit.

- [ ] **Step 1: Write the failing tests**

`tests/test_central_worker.py`:
```python
import copy
import threading

import pytest

from core.central_state import CentralState
from core.central_sync import (RETRY_AFTER_FAILURE, SyncResult, SyncWorker,
                               choose_pipeline_profile)
from core.profile_manager import DEFAULT_PROJECT, ProfileManager
from data.db import Database


class FakeService:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0
        self.called = threading.Event()

    def sync_once(self):
        self.calls += 1
        self.called.set()
        r = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture()
def state(tmp_path):
    st = CentralState(tmp_path)
    st.register_result("https://c.example", "pc-1", "k", 600)
    return st


def worker(service, state, on_result=None):
    return SyncWorker(service, state, on_result=on_result)


# ---- next_wait (pure) ---------------------------------------------------------------
def test_next_wait_policy(state):
    w = worker(FakeService([SyncResult("ok")]), state)
    assert w.next_wait(SyncResult("ok")) == 600.0
    assert w.next_wait(SyncResult("unavailable")) == RETRY_AFTER_FAILURE
    state.update(poll_interval_seconds=60)
    assert w.next_wait(SyncResult("unavailable")) == 60.0          # min(interval, retry)
    assert w.next_wait(SyncResult("auth_failed")) is None
    assert w.next_wait(SyncResult("not_registered")) is None
    assert RETRY_AFTER_FAILURE >= 120.0


# ---- thread behaviour -----------------------------------------------------------------
def test_initial_sync_runs_once_then_waits(state):
    svc = FakeService([SyncResult("ok")])
    got = []
    w = worker(svc, state, on_result=got.append)
    w.start(initial_sync=True)
    try:
        assert svc.called.wait(5)
        assert got and got[0].status == "ok"
        assert svc.calls == 1                   # then it waits for the poll interval
    finally:
        w.stop()


def test_initial_sync_false_does_not_sync_until_woken(state):
    svc = FakeService([SyncResult("ok")])
    w = worker(svc, state)
    w.start(initial_sync=False)
    try:
        assert not svc.called.wait(0.3)
        w.sync_now()
        assert svc.called.wait(5)
    finally:
        w.stop()


def test_sync_now_triggers_another_sync(state):
    svc = FakeService([SyncResult("ok")])
    w = worker(svc, state)
    w.start(initial_sync=True)
    try:
        assert svc.called.wait(5)
        svc.called.clear()
        w.sync_now()
        assert svc.called.wait(5) and svc.calls == 2
    finally:
        w.stop()


def test_auth_failed_stops_polling_until_woken(state):
    svc = FakeService([SyncResult("auth_failed")])
    w = worker(svc, state)
    w.start(initial_sync=True)
    try:
        assert svc.called.wait(5)
        svc.called.clear()
        assert not svc.called.wait(0.5)         # no timer-driven retry
        w.sync_now()
        assert svc.called.wait(5)
    finally:
        w.stop()


def test_exceptions_in_service_and_callback_never_kill_the_thread(state):
    svc = FakeService([RuntimeError("boom"), SyncResult("ok")])
    got = []

    def bad_callback(r):
        got.append(r)
        raise RuntimeError("ui exploded")

    w = worker(svc, state, on_result=bad_callback)
    w.start(initial_sync=True)
    try:
        assert svc.called.wait(5)
        assert got and got[0].status == "unavailable" and "boom" in got[0].message
        svc.called.clear()
        w.sync_now()
        assert svc.called.wait(5)               # thread still alive
    finally:
        w.stop()


def test_stop_is_prompt_and_idempotent(state):
    svc = FakeService([SyncResult("ok")])
    w = worker(svc, state)
    w.start(initial_sync=True)
    assert svc.called.wait(5)
    w.stop()
    w.stop()
    assert not w.is_running()


def test_stop_before_start_is_safe(state):
    worker(FakeService([SyncResult("ok")]), state).stop()


# ---- choose_pipeline_profile -----------------------------------------------------------
@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "w.db")
    yield d
    d.close()


def _project(pm, pid, name):
    cfg = copy.deepcopy(DEFAULT_PROJECT)
    cfg.update(project_id=pid, name=name, created="2026-10-09T00:00:00+00:00")
    cfg["rules"]["rule_set_id"] = "r"
    return pm.adopt(cfg)


def test_choose_profile_prefers_first_managed_project(db, tmp_path):
    pm = ProfileManager(tmp_path / "p", db)
    _project(pm, "aaaa", "Alpha local")
    _project(pm, "bbbb", "Zulu central")
    db.set_central_project("bbbb", 1)
    assert choose_pipeline_profile(db, fallback=lambda: {"x": 1})["project_id"] == "bbbb"


def test_choose_profile_ignores_unmanaged_and_falls_back(db, tmp_path):
    pm = ProfileManager(tmp_path / "p", db)
    _project(pm, "aaaa", "Alpha")
    db.set_central_project("aaaa", 1, managed=False)
    assert choose_pipeline_profile(db, fallback=lambda: {"fallback": True}) == {"fallback": True}


def test_choose_profile_skips_inactive_managed_projects(db, tmp_path):
    pm = ProfileManager(tmp_path / "p", db)
    cfg = _project(pm, "aaaa", "Alpha")
    cfg["status"] = "archived"
    pm.save(cfg)
    db.set_central_project("aaaa", 1)
    assert choose_pipeline_profile(db, fallback=lambda: {"fb": 1}) == {"fb": 1}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/test_central_worker.py`
Expected: FAIL — `ImportError: cannot import name 'SyncWorker'`.

- [ ] **Step 3: Implement**

Append to `core/central_sync.py` (add `import threading` and `from typing import Callable`):
```python
RETRY_AFTER_FAILURE = 120.0


class SyncWorker:
    """Polls central on a daemon thread. Never raises into the app."""

    def __init__(self, service: CentralSyncService, state: CentralState,
                 on_result: Callable[[SyncResult], None] | None = None):
        self._service, self._state, self._on_result = service, state, on_result
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    # -- policy (pure) -----------------------------------------------------------
    def next_wait(self, result: SyncResult | None) -> float | None:
        """Seconds to sleep before the next sync; None = until woken."""
        s = self._state.settings
        if result is None:
            if not self._state.is_registered() or s.auth_failed:
                return None
            return float(s.poll_interval_seconds)
        if result.status in ("not_registered", "auth_failed"):
            return None
        if result.status == "unavailable":
            return min(float(s.poll_interval_seconds), RETRY_AFTER_FAILURE)
        return float(s.poll_interval_seconds)

    # -- lifecycle ----------------------------------------------------------------
    def start(self, initial_sync: bool = True) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, args=(initial_sync,), name="central-sync",
            daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        t = self._thread
        if t is not None:
            t.join(timeout=5)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def sync_now(self) -> None:
        self._wake.set()

    # -- loop ------------------------------------------------------------------------
    def _run(self, initial_sync: bool) -> None:
        result = self._do_sync() if initial_sync else None
        while not self._stop.is_set():
            self._wake.wait(self.next_wait(result))
            if self._stop.is_set():
                break
            self._wake.clear()
            result = self._do_sync()

    def _do_sync(self) -> SyncResult:
        try:
            result = self._service.sync_once()
        except Exception as e:                       # belt and braces
            log.exception("central sync crashed")
            result = SyncResult("unavailable",
                                message=f"{type(e).__name__}: {e}")
        if self._on_result is not None:
            try:
                self._on_result(result)
            except Exception:
                log.exception("central sync result handler failed")
        return result


def choose_pipeline_profile(db, fallback: Callable[[], dict]) -> dict:
    """The pipeline binds to ONE project at startup. A PC that is managed
    centrally uses its first active managed project; otherwise (or if none
    is usable) the existing first-run behaviour applies."""
    managed = {r["project_id"] for r in
               db.list_central_projects(managed_only=True)}
    for row in db.list_projects("active"):
        if row["project_id"] in managed:
            return json.loads(row["config_json"])
    return fallback()
```
`core/event_bus.py`: add `central_synced = pyqtSignal(object)  # SyncResult` to `EventBus`.

`main.py` (inside `main()`; add imports `from core.central_state import CentralState`, `from core.central_sync import CentralSyncService, SyncWorker, choose_pipeline_profile`): after `login`, replace `profile = build_default_project(pm, db, root)` by
```python
    central_state = CentralState(root)
    sync_service = CentralSyncService(
        db, pm, central_state, root, audit=audit,
        operator_fn=lambda: (Session.current_user().username
                             if Session.current_user() else ""),
        timeout=5.0)
    if central_state.is_registered():
        try:                       # short, bounded: never delays scanning for long
            sync_service.sync_once()
        except Exception:
            logging.exception("startup central sync failed; continuing")
    sync_service.timeout = 15.0
    profile = choose_pipeline_profile(
        db, fallback=lambda: build_default_project(pm, db, root))
```
After `bus = EventBus()` / `pipeline.start()`:
```python
    sync_worker = SyncWorker(sync_service, central_state,
                             on_result=bus.central_synced.emit)
    sync_worker.start(initial_sync=False)   # startup sync already ran above
```
and pass `central_state=central_state, sync_service=sync_service, sync_worker=sync_worker` to `MainWindow(...)` (the kwargs are consumed in Task 7); call `sync_worker.stop()` next to `backup_service.stop()` on exit.

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/test_central_worker.py tests/test_central_sync.py`
Expected: all pass. Run each thread test 5× (`-p no:randomly` not needed) to confirm they are not flaky: `for ($i=0;$i -lt 5;$i++){ .\.venv\Scripts\python.exe -m pytest tests/test_central_worker.py -q }`.

- [ ] **Step 5: Commit**

```bash
git add core/central_sync.py core/event_bus.py main.py tests/test_central_worker.py
git commit -m "feat(central-sync): background worker, startup sync and profile choice"
```

---

### Task 7: Admin "Central" tab and Workflow Designer guard

**Files:**
- Create: `ui/central_tab.py`
- Modify: `ui/admin_panel.py`, `ui/main_window.py`, `ui/workflow_designer.py`
- Test: `tests/test_central_ui.py`

**Interfaces:**
- Consumes: `CentralState`, `CentralSyncService` (`register`, `disconnect`), `SyncWorker` (`sync_now`), `EventBus.central_synced`, `SyncResult`, `Database.get_central_project`, `core.rbac.User.can("user.manage")`.
- Produces: `CentralTab(state, service, worker, current_user, bus=None, parent=None)` with widgets `url_edit`, `code_edit`, `register_btn`, `sync_btn`, `disconnect_btn`, `status_label`, `projects_table` and methods `refresh_status(result=None)`, `register()`, `disconnect()` (the confirmation is a `QMessageBox.question` that tests monkeypatch), `status_text(result=None) -> str`; `AdminPanel(..., central_state=None, central_service=None, central_worker=None, bus=None)` adds a "Central" tab only when `central_state` is given; `MainWindow(..., central_state=None, sync_service=None, sync_worker=None)` forwards them; `WorkflowDesigner.save()` refuses to save a project that is centrally managed.

- [ ] **Step 1: Write the failing tests**

Read `tests/test_phase2_ui.py` and `tests/test_ui_smoke.py` first to copy their fixture style (`ADMIN`/`VIEWER` users, `qtbot`, monkeypatching `QMessageBox`). `tests/test_central_ui.py`:
```python
import copy

import pytest

from core.central_client import CentralRegistrationError, RegisterResult
from core.central_state import CentralState
from core.central_sync import CentralSyncService, SyncResult
from core.profile_manager import DEFAULT_PROJECT, ProfileManager
from core.rbac import User
from data.db import Database
from ui.admin_panel import AdminPanel
from ui.central_tab import CentralTab

ADMIN = User("u1", "root", "admin")
VIEWER = User("u2", "view", "viewer")


class StubClient:
    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.codes = result, exc, []

    def register(self, code):
        self.codes.append(code)
        if self.exc:
            raise self.exc
        return self.result


class StubWorker:
    def __init__(self):
        self.woken = 0

    def sync_now(self):
        self.woken += 1


@pytest.fixture()
def env(tmp_path):
    db = Database(tmp_path / "ui.db")
    pm = ProfileManager(tmp_path / "projects", db)
    state = CentralState(tmp_path)
    holder = {"client": StubClient(RegisterResult("pc-1", "dsk_K", 600))}
    svc = CentralSyncService(db, pm, state, tmp_path,
                             client_factory=lambda url, t: holder["client"])
    worker = StubWorker()
    yield type("E", (), dict(db=db, pm=pm, state=state, svc=svc,
                             worker=worker, holder=holder, root=tmp_path))
    db.close()


def tab(env, user=ADMIN, qtbot=None):
    t = CentralTab(env.state, env.svc, env.worker, user)
    if qtbot:
        qtbot.addWidget(t)
    return t


def test_unregistered_status_and_gating(qtbot, env):
    t = tab(env, qtbot=qtbot)
    assert "not registered" in t.status_text().lower()
    assert t.register_btn.isEnabled() and not t.sync_btn.isEnabled()
    assert not t.disconnect_btn.isEnabled()
    v = tab(env, VIEWER, qtbot)
    assert not v.register_btn.isEnabled() and not v.disconnect_btn.isEnabled()


def test_register_success_updates_state_and_wakes_worker(qtbot, env):
    t = tab(env, qtbot=qtbot)
    t.url_edit.setText("https://central.example")
    t.code_edit.setText("ABCDEFGH1234")
    t.register()
    assert env.state.is_registered() and env.worker.woken == 1
    assert t.code_edit.text() == ""                       # one-time code is cleared
    assert t.sync_btn.isEnabled() and t.disconnect_btn.isEnabled()
    assert "pc-1" in t.status_text()


def test_register_rejects_http_url_with_message(qtbot, env, monkeypatch):
    shown = []
    monkeypatch.setattr("ui.central_tab.QMessageBox.warning",
                        staticmethod(lambda *a, **k: shown.append(a[2])))
    from core.central_sync import default_client_factory
    env.svc._client_factory = default_client_factory
    t = tab(env, qtbot=qtbot)
    t.url_edit.setText("http://central.example")
    t.code_edit.setText("ABCDEFGH1234")
    t.register()
    assert not env.state.is_registered() and shown and "https" in shown[0].lower()


def test_register_bad_code_shows_message_and_keeps_code(qtbot, env, monkeypatch):
    shown = []
    monkeypatch.setattr("ui.central_tab.QMessageBox.warning",
                        staticmethod(lambda *a, **k: shown.append(a[2])))
    env.holder["client"] = StubClient(exc=CentralRegistrationError("bad"))
    t = tab(env, qtbot=qtbot)
    t.url_edit.setText("https://central.example")
    t.code_edit.setText("ABCDEFGH1234")
    t.register()
    assert not env.state.is_registered() and shown
    assert env.worker.woken == 0


def test_register_requires_both_fields(qtbot, env, monkeypatch):
    monkeypatch.setattr("ui.central_tab.QMessageBox.warning",
                        staticmethod(lambda *a, **k: None))
    t = tab(env, qtbot=qtbot)
    t.url_edit.setText("https://central.example")
    t.register()
    assert env.holder["client"].codes == []


def test_auth_failed_banner_and_reenroll(qtbot, env):
    env.state.register_result("https://central.example", "pc-1", "k", 600)
    env.state.update(auth_failed=True)
    t = tab(env, qtbot=qtbot)
    assert "enrollment code" in t.status_text().lower()
    assert t.register_btn.isEnabled()                      # can re-enroll while registered


def test_status_reports_result_and_restart_hint(qtbot, env):
    env.state.register_result("https://central.example", "pc-1", "k", 600)
    t = tab(env, qtbot=qtbot)
    assert "restart" not in t.status_text(SyncResult("ok", unchanged=1)).lower()
    text = t.status_text(SyncResult("ok", applied=["p1"]))
    assert "restart" in text.lower()
    assert "unavailable" in t.status_text(SyncResult("unavailable", message="dns")).lower()
    assert "skipped" in t.status_text(SyncResult("ok", errors={"p2": "bad"})).lower()


def test_projects_table_lists_managed_state(qtbot, env):
    cfg = copy.deepcopy(DEFAULT_PROJECT)
    cfg.update(project_id="aaaa", name="Invoices",
               created="2026-10-09T00:00:00+00:00")
    cfg["rules"]["rule_set_id"] = "r"
    env.pm.adopt(cfg)
    env.db.set_central_project("aaaa", 3)
    t = tab(env, qtbot=qtbot)
    t.db = env.db
    t.refresh_status()
    assert t.projects_table.rowCount() == 1
    assert t.projects_table.item(0, 0).text() == "Invoices"
    assert "3" in t.projects_table.item(0, 1).text()


def test_disconnect_asks_for_confirmation(qtbot, env, monkeypatch):
    env.state.register_result("https://central.example", "pc-1", "k", 600)
    t = tab(env, qtbot=qtbot)
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr("ui.central_tab.QMessageBox.question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    t.disconnect()
    assert env.state.is_registered()
    monkeypatch.setattr("ui.central_tab.QMessageBox.question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    t.disconnect()
    assert not env.state.is_registered()


def test_bus_signal_refreshes_status(qtbot, env):
    from core.event_bus import EventBus
    bus = EventBus()
    env.state.register_result("https://central.example", "pc-1", "k", 600)
    t = CentralTab(env.state, env.svc, env.worker, ADMIN, bus=bus)
    qtbot.addWidget(t)
    bus.central_synced.emit(SyncResult("ok", applied=["p1"]))
    assert "restart" in t.status_label.text().lower()


# ---- AdminPanel hosting -------------------------------------------------------------------
def test_admin_panel_has_central_tab_only_when_wired(qtbot, env):
    plain = AdminPanel(env.db, ADMIN)
    qtbot.addWidget(plain)
    assert not hasattr(plain, "central_tab")
    wired = AdminPanel(env.db, ADMIN, central_state=env.state,
                       central_service=env.svc, central_worker=env.worker)
    qtbot.addWidget(wired)
    assert isinstance(wired.central_tab, CentralTab)


# ---- Workflow Designer guard ----------------------------------------------------------------
def test_designer_refuses_to_save_a_centrally_managed_project(qtbot, env, monkeypatch):
    from ui.workflow_designer import WorkflowDesigner
    cfg = env.pm.create("Managed")
    env.db.set_central_project(cfg["project_id"], 1)
    d = WorkflowDesigner(env.db, env.pm, ADMIN)
    qtbot.addWidget(d)
    shown = []
    monkeypatch.setattr("ui.workflow_designer.QMessageBox.warning",
                        staticmethod(lambda *a, **k: shown.append(a[2])))
    d.load_project(cfg["project_id"]) if hasattr(d, "load_project") else None
    before = env.pm.load(cfg["project_id"])["version"]
    d.save()
    assert shown and "central" in shown[0].lower()
    assert env.pm.load(cfg["project_id"])["version"] == before


def test_designer_still_saves_unmanaged_and_unassigned_projects(qtbot, env, monkeypatch):
    from ui.workflow_designer import WorkflowDesigner
    cfg = env.pm.create("Local")
    env.db.set_central_project(cfg["project_id"], 1, managed=False)   # unassigned
    d = WorkflowDesigner(env.db, env.pm, ADMIN)
    qtbot.addWidget(d)
    monkeypatch.setattr("ui.workflow_designer.QMessageBox.information",
                        staticmethod(lambda *a, **k: None))
    before = env.pm.load(cfg["project_id"])["version"]
    d.save()
    assert env.pm.load(cfg["project_id"])["version"] == before + 1
```
The two Designer tests depend on how `WorkflowDesigner` selects its current project: read `ui/workflow_designer.py` (`refresh_projects`, `project_combo`, `self.config`, `collect_config`, `self.db`) and adapt the test set-up so the designer is actually showing the intended project (select it in `project_combo` / call its project-loading method) — the assertions stay as written. If `WorkflowDesigner` does not keep `self.db`, store it in `__init__` (it receives `db`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/test_central_ui.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'ui.central_tab'`.

- [ ] **Step 3: Implement**

`ui/central_tab.py` (follow `ui/admin_panel.py` style, `from ui.theme import mark`):
```python
"""Admin > Central: enrol this PC with the central service and show sync
status (spec: sync client). Admin-only (user.manage)."""
from __future__ import annotations

from PyQt6.QtWidgets import (QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from core.central_client import CentralError, CentralRegistrationError
from core.central_sync import SyncResult
from ui.theme import mark


class CentralTab(QWidget):
    def __init__(self, state, service, worker, current_user, bus=None,
                 parent=None):
        super().__init__(parent)
        self.state, self.service, self.worker = state, service, worker
        self.db = getattr(service, "db", None)
        self._last_result: SyncResult | None = None
        can = current_user is not None and current_user.can("user.manage")
        self._can = can

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.url_edit = QLineEdit(state.settings.server_url)
        self.url_edit.setPlaceholderText("https://central.example.com")
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("one-time enrollment code")
        form.addRow("Central server", self.url_edit)
        form.addRow("Enrollment code", self.code_edit)
        layout.addLayout(form)

        row = QHBoxLayout()
        self.register_btn = QPushButton("Register this PC")
        mark(self.register_btn, "primary")
        self.register_btn.clicked.connect(self.register)
        self.sync_btn = QPushButton("Sync now")
        self.sync_btn.clicked.connect(self.sync_now)
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(self.disconnect)
        for b in (self.register_btn, self.sync_btn, self.disconnect_btn):
            row.addWidget(b)
        layout.addLayout(row)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.projects_table = QTableWidget(0, 3)
        self.projects_table.setHorizontalHeaderLabels(
            ["Project", "Central version", "Managed"])
        self.projects_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.projects_table, stretch=1)

        if bus is not None:
            bus.central_synced.connect(self._on_synced)
        self.refresh_status()

    # -- status ---------------------------------------------------------------
    def status_text(self, result: SyncResult | None = None) -> str:
        s = self.state.settings
        if not self.state.is_registered():
            return "This PC is not registered with a central server."
        lines = [f"Registered as PC {s.pc_id} at {s.server_url}."]
        if s.auth_failed:
            lines.append("Central rejected this PC's key. Ask an administrator "
                         "for a new enrollment code and register again.")
        elif s.last_sync_at:
            lines.append(f"Last sync {s.last_sync_at}: {s.last_sync_status}.")
        if result is not None:
            if result.status == "unavailable":
                lines.append(f"Central is unavailable ({result.message}); "
                             "using the last known configuration.")
            if result.errors:
                lines.append(f"{len(result.errors)} project(s) were skipped: "
                             + "; ".join(f"{k}: {v}" for k, v in
                                         result.errors.items()))
            if result.restart_needed:
                lines.append("Updated project settings were received. "
                             "Restart DOKScanner to apply them to scanning.")
        return "\n".join(lines)

    def refresh_status(self, result: SyncResult | None = None) -> None:
        if result is not None:
            self._last_result = result
        registered = self.state.is_registered()
        self.status_label.setText(self.status_text(self._last_result))
        self.register_btn.setEnabled(self._can)
        self.sync_btn.setEnabled(self._can and registered
                                 and not self.state.settings.auth_failed)
        self.disconnect_btn.setEnabled(self._can and registered)
        self._fill_projects()

    def _fill_projects(self) -> None:
        rows = []
        if self.db is not None:
            for rec in self.db.list_central_projects():
                p = self.db.get_project(rec["project_id"])
                rows.append((p["name"] if p else rec["project_id"],
                             f"v{rec['central_version']}",
                             "yes" if rec["managed"] else "no (unassigned)"))
        self.projects_table.setRowCount(len(rows))
        for r, vals in enumerate(rows):
            for c, v in enumerate(vals):
                self.projects_table.setItem(r, c, QTableWidgetItem(v))

    def _on_synced(self, result) -> None:
        self.refresh_status(result)

    # -- actions ------------------------------------------------------------------
    def register(self) -> None:
        url, code = self.url_edit.text().strip(), self.code_edit.text().strip()
        if not url or not code:
            QMessageBox.warning(self, "Central",
                                "Enter the server address and the enrollment code.")
            return
        try:
            self.service.register(url, code)
        except ValueError as e:
            QMessageBox.warning(self, "Central", str(e))
            return
        except CentralRegistrationError as e:
            QMessageBox.warning(self, "Central", str(e))
            return
        except CentralError as e:
            QMessageBox.warning(self, "Central",
                                f"Could not reach the central server: {e}")
            return
        self.code_edit.clear()
        self.worker.sync_now()
        self.refresh_status()

    def sync_now(self) -> None:
        self.worker.sync_now()

    def disconnect(self) -> None:
        answer = QMessageBox.question(
            self, "Disconnect",
            "Disconnect this PC from central? Existing projects stay on this "
            "PC but will no longer be updated.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.service.disconnect()
        self._last_result = None
        self.refresh_status()
```
`ui/admin_panel.py`: extend `AdminPanel.__init__` signature with `central_state=None, central_service=None, central_worker=None, bus=None`; when `central_state is not None`: `self.central_tab = CentralTab(central_state, central_service, central_worker, current_user, bus=bus)` and `tabs.addTab(self.central_tab, "Central")`.
`ui/main_window.py`: add the three kwargs to `MainWindow.__init__` and pass them (plus `bus`) where `AdminPanel(...)` is built (grep `AdminPanel(`).
`ui/workflow_designer.py` `save()`: first lines inside `save` (before collecting the config):
```python
        rec = self.db.get_central_project(self.config["project_id"]) \
            if getattr(self, "config", None) else None
        if rec is not None and rec["managed"]:
            QMessageBox.warning(
                self, "Managed centrally",
                "This project is managed by the central server; local changes "
                "would be overwritten at the next sync. Change it in the "
                "central dashboard instead.")
            return
```
(use whatever attribute the designer already uses for the current project id; add `self.db = db` in `__init__` if missing.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/test_central_ui.py tests/test_phase2_ui.py tests/test_ui_smoke.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add ui/central_tab.py ui/admin_panel.py ui/main_window.py ui/workflow_designer.py tests/test_central_ui.py
git commit -m "feat(central-sync): Central admin tab and designer guard for managed projects"
```

---

### Task 8: End-to-end test against the real central app, docs, packaging check

**Files:**
- Create: `tests/test_central_integration.py`
- Modify: `central/README.md`, `docs/UAT-checklist.md`, `docs/superpowers/specs/2026-10-04-central-api-threat-model.md`, `docs/ENHANCEMENT_PLAN_STATUS.md`

**Interfaces:**
- Consumes: everything above plus `central.app.create_app`, `central.settings.Settings`, `central.manage.create_admin`, `fastapi.testclient.TestClient`.
- Produces: one skippable integration test module; documentation updates.

- [ ] **Step 1: Write the integration test**

`tests/test_central_integration.py` (the module must skip cleanly when the central dependencies are not installed — use `pytest.importorskip` for `fastapi`, `sqlalchemy`, `httpx`, `itsdangerous`; read `tests/central/conftest.py` for the bcrypt-cheapening trick and replicate it with `monkeypatch` so the test is fast):
```python
import copy

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("httpx")
pytest.importorskip("itsdangerous")

from fastapi.testclient import TestClient  # noqa: E402

from central.app import create_app  # noqa: E402
from central.manage import create_admin  # noqa: E402
from central.settings import Settings  # noqa: E402
from core.central_client import CentralClient  # noqa: E402
from core.central_state import CentralState  # noqa: E402
from core.central_sync import CentralSyncService  # noqa: E402
from core.profile_manager import ProfileManager  # noqa: E402
from data.db import Database  # noqa: E402

BASE = "https://central.test"


def make_transport(test_client):
    """Route CentralClient requests into the in-process central app."""
    def transport(method, url, headers, body, timeout):
        assert url.startswith(BASE)
        r = test_client.request(method, url[len(BASE):], headers=headers,
                                content=body)
        return r.status_code, r.content
    return transport


@pytest.fixture()
def stack(tmp_path, monkeypatch):
    import bcrypt
    real = bcrypt.gensalt
    monkeypatch.setattr("central.security.bcrypt.gensalt",
                        lambda rounds=4, prefix=b"2b": real(rounds=4, prefix=prefix))
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'central.db'}",
                        session_secret="s" * 40, cookie_secure=False)
    app = create_app(settings)
    create_admin(app.state.session_factory, "root", "correct horse battery")
    admin = TestClient(app)
    token = admin.post("/admin/login", json={
        "username": "root", "password": "correct horse battery"}).json()["csrf_token"]
    admin.headers["X-CSRF-Token"] = token
    pc_http = TestClient(app)                       # separate cookie jar: the PC

    pc_dir = tmp_path / "pc"
    pc_dir.mkdir()
    db = Database(pc_dir / "pc.db")
    pm = ProfileManager(pc_dir / "projects", db)
    state = CentralState(pc_dir)
    svc = CentralSyncService(
        db, pm, state, pc_dir,
        client_factory=lambda url, t: CentralClient(
            url, transport=make_transport(pc_http), timeout=t))
    yield type("S", (), dict(admin=admin, db=db, pm=pm, state=state, svc=svc,
                             tmp=tmp_path, root=pc_dir))
    db.close()
    app.state.engine.dispose()


def central_project_config(tmp_path):
    return ProfileManager(tmp_path / "central_pm").create("Seed")


def test_full_round_trip(stack):
    s = stack
    # admin: enrol a PC, publish a project, push it
    pc = s.admin.post("/admin/pcs", json={"display_name": "Counter-1"}).json()
    s.svc.register(BASE, pc["enrollment_code"])
    assert s.state.is_registered()

    cfg = central_project_config(s.tmp)
    cfg["ocr"]["lang"] = "deu"
    pid = "aaaaaaaa-1111-2222-3333-444444444444"
    r = s.admin.put(f"/admin/projects/{pid}", json={"name": "Invoices", "config": cfg})
    assert r.status_code == 200
    s.admin.post(f"/admin/projects/{pid}/push", json={"pc_ids": [pc["pc_id"]]})

    # PC: first sync applies it with seeded local sections
    res = s.svc.sync_once()
    assert res.status == "ok" and res.applied == [pid] and res.restart_needed
    local = s.pm.load(pid)
    assert local["ocr"]["lang"] == "deu" and local["name"] == "Invoices"
    assert local["export"]["output_path"].endswith("output")

    # the PC customises its own delivery path; central edits the project
    local["delivery"]["local_folder"]["path"] = "D:/pc-specific/out"
    s.pm.save(local)
    cfg2 = copy.deepcopy(cfg)
    cfg2["ocr"]["lang"] = "fra"
    s.admin.put(f"/admin/projects/{pid}", json={"name": "Invoices", "config": cfg2})
    res = s.svc.sync_once()
    assert res.applied == [pid]
    after = s.pm.load(pid)
    assert after["ocr"]["lang"] == "fra"
    assert after["delivery"]["local_folder"]["path"] == "D:/pc-specific/out"

    # nothing changed -> nothing applied
    assert s.svc.sync_once().unchanged == 1

    # admin unassigns -> PC keeps the project but stops managing it
    assert s.admin.delete(f"/admin/projects/{pid}/assignments/{pc['pc_id']}").status_code == 204
    res = s.svc.sync_once()
    assert res.unassigned == [pid]
    assert s.db.get_central_project(pid)["managed"] == 0 and s.pm.load(pid)

    # admin revokes the key -> auth failure persisted, no further calls
    s.admin.post(f"/admin/projects/{pid}/push", json={"pc_ids": [pc["pc_id"]]})
    s.admin.delete(f"/admin/pcs/{pc['pc_id']}/key")
    assert s.svc.sync_once().status == "auth_failed"
    assert s.state.settings.auth_failed is True

    # re-enrol with a fresh code -> service restored
    code = s.admin.post(f"/admin/pcs/{pc['pc_id']}/enrollment-code").json()["enrollment_code"]
    s.svc.register(BASE, code)
    assert s.state.settings.auth_failed is False
    assert s.svc.sync_once().status == "ok"


def test_central_credentials_rejection_is_visible_to_the_admin_not_the_pc(stack):
    cfg = central_project_config(stack.tmp)
    cfg["delivery"]["sftp"] = {"enabled": True, "host": "h", "password": "pw"}
    r = stack.admin.put("/admin/projects/bbbbbbbb-1111-2222-3333-444444444444",
                        json={"name": "Leaky", "config": cfg})
    assert r.status_code == 422
```

- [ ] **Step 2: Run the integration test**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/test_central_integration.py`
Expected: pass (or skip with a clear reason where central deps are absent). If `test_full_round_trip` fails on a contract mismatch between the client and the real API, fix the CLIENT (plan 1's API is the contract) unless the API is demonstrably wrong — then stop and report.

- [ ] **Step 3: Update docs**

- `central/README.md` "Enrolling a PC": step 2 now describes the Admin → Central tab (server URL + code → Register), that the API key is DPAPI-protected, that updated projects apply to scanning after the next DOKScanner restart, and that a PC keeps its own scanner/export/delivery settings.
- `docs/UAT-checklist.md`: add a "Central sync (multi-PC)" section: register with a code; project appears after sync; edit on central → new version arrives and Designer refuses local save; unassign keeps local project; revoke key → banner + no polling; server down at startup → app scans normally; disconnect keeps projects.
- `docs/superpowers/specs/2026-10-04-central-api-threat-model.md`: add a short "Client side (DOKScanner.exe)" section: HTTPS-only/no redirects/TLS verify, key in DPAPI, project_id path-safety, response size cap, local-only sections never taken from central, no credentials from central, startup sync bounded to 5 s, restart-to-apply, residual risks (compromised central can push arbitrary processing config incl. rules; no config signing; operator id self-asserted).
- `docs/ENHANCEMENT_PLAN_STATUS.md`: mark plan 2 built; record the two known limitations: (1) the pipeline binds one project at startup, so changes apply after restart; (2) **the scan wizard's project choice is not applied to the pipeline today** (pre-existing; `Pipeline.profile` is fixed) — a separate plan is needed before multi-project PCs are meaningful.

- [ ] **Step 4: Full verification**

Run the COMPLETE suite: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest` — Expected: everything passes (≥ 550 + the new tests); `.\.venv\Scripts\python.exe scripts/security_scan.py` and triage any new findings in `core/central_*.py` in `docs/security/SAST_FINDINGS_REPORT.md`; `pyinstaller` build is not required, but run `tests/test_packaging_spec.py` (included in the full suite) to confirm nothing new needs bundling.

- [ ] **Step 5: Commit**

```bash
git add tests/test_central_integration.py central/README.md docs
git commit -m "test+docs(central-sync): end-to-end round trip against the central app, docs and threat model"
```

---

## Self-Review

**Spec coverage** (central dashboard spec, "Sync behavior" + "Security notes" + decisions): startup + periodic pull (Tasks 5–6); newer-version-only apply (Task 5 `_apply_entry`); written via existing project machinery with local audit (Tasks 2, 5); unreachable → last-known-good, never blocks scanning (Tasks 3, 5, 6); unregistered PC skips sync entirely (Task 5/6, constraint); API key in DPAPI (Task 1); HTTPS (Task 3); operator header for audit (Tasks 3, 5, 6); admin-triggered registration with one-time code (Tasks 5, 7); poll interval from server (Tasks 3, 5, 6). Out of scope by spec/plan: heartbeat (optional in Phase 1; the pull already refreshes `last_seen_at`), templates, per-operator assignment.

**Placeholder scan:** none; Task 7's two Designer tests explicitly tell the implementer to adapt set-up to the designer's real project-selection API while keeping assertions.

**Type consistency:** `SyncResult`, `CentralSyncService(db, pm, state, root, audit, client_factory, operator_fn, timeout)`, `client_factory(url, timeout)`, `CentralState.register_result/update/clear`, `Database.set/get/list/unmanage_*central_project*`, `ProfileManager.adopt`, `PullResult.projects: list[dict]`, `validate_entry/overlay/default_local_sections` are used with the same signatures in Tasks 4–8.

**Review Focus coverage:** path traversal (Tasks 2, 4, 5), unreachable/bounded retries (Tasks 3, 5, 6), revoked key persisted + banner + re-enrol (Tasks 5, 6, 7, 8), schema-invalid project skipped (Task 5), local-only sections + designer guard (Tasks 4, 5, 7).
