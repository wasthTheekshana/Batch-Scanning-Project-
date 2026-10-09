# Central Service API (Phase 1, Plan 1 of 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `dokscanner-central`, the FastAPI + Postgres backend that lets an admin manage projects/templates once and lets every registered PC pull its assigned config.

**Architecture:** A new top-level `central/` package in this repo, deployed separately from DOKScanner.exe. SQLAlchemy 2.0 models (Postgres in production, SQLite in tests via `CENTRAL_DATABASE_URL`). Two caller types: admins (session cookie + CSRF header) and PCs (Bearer API key, issued by exchanging a one-time enrollment code). Config is opaque versioned JSON; central never sees scanned documents.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.0, psycopg 3, Starlette `SessionMiddleware` (itsdangerous), bcrypt, jsonschema (via `core.profile_manager`), pytest + httpx `TestClient`.

**Spec:** `docs/superpowers/specs/2026-08-29-central-admin-dashboard-design.md` (see its "Decisions resolved 2026-10-04" section). This is plan 1 of 3: (1) this API, (2) sync client in DOKScanner.exe, (3) React dashboard.

## Global Constraints

- Same repo, new top-level `central/` dir; tests in `tests/central/`. Deployed separately from DOKScanner.exe.
- FastAPI backend; Postgres in production; SQLite acceptable for tests only.
- Admin auth: session cookie (HttpOnly, SameSite=strict, `Secure` unless `CENTRAL_COOKIE_SECURE=0`), CSRF header `X-CSRF-Token` on every non-GET admin call. No JWT.
- PC registration: admin-triggered only. Admin creates the PC and gets a one-time enrollment code; `POST /pcs/register` exchanges it for the API key. No self-registration.
- API keys and enrollment codes are stored only as SHA-256 hashes; admin passwords as bcrypt.
- Poll interval: default 600 seconds, one global admin-configurable value (per-PC override is out of scope).
- Central DB stores no scanned documents, images, or per-PC operational data (batches, jobs, custody events).
- All traffic is HTTPS; TLS terminates at the reverse proxy in front of uvicorn.
- Must meet the bar in `docs/security/SDLC_POLICY.md` (parameterized queries only, no hard-coded secrets, threat model before exposure).
- `central/` must not break the existing DOKScanner.exe test run: its tests skip when `central/requirements.txt` deps are absent. `central/` may import only `core.profile_manager` from the exe codebase.
- End every commit message with the `Co-Authored-By` trailer given in the session's attribution rule.

## Cross-plan contracts (plans 2 and 3 rely on these)

- `GET /pcs/{pc_id}/config` returns `{"pc_id": str, "poll_interval_seconds": int, "projects": [{"project_id": str, "name": str, "version": int, "config": dict}]}`. `version` is central's per-project integer, starting at 1, bumped only when the config JSON actually changes.
- Auth for PC calls: `Authorization: Bearer <api_key>`; optional `X-Operator-Id` header (audit only).
- **As built (2026-10-04, after the final review) — plan 2 must honour these:**
  - **Identity & enrollment:** a PC learns its own id and key only from `POST /pcs/register {"code": ...}` → `{pc_id, api_key, poll_interval_seconds}` (code is single-use, 60 min). The server URL is out-of-band (installer / Admin panel). Store `pc_id` + `api_key` (DPAPI keystore) and use `pc_id` in every path.
  - **Error meanings:** `400` on register = bad/used/expired code (indistinguishable by design); `401` on a PC call = key missing, revoked or replaced → the PC must re-enroll (admin issues a new code); `403` = the key belongs to another PC (misconfigured `pc_id`). Never retry 401/403 in a loop; log and keep running on the last-known-good local config. `5xx`/network errors: back off and keep the local config (never block scanning).
  - **Versions:** compare ONLY the outer `version` (central's per-project integer) against the version stored locally. The `version` key embedded inside `config` is the project file's own counter (`ProfileManager.save` bumps it on the PC) and must NOT be used for sync decisions; store the central version separately.
  - **Unassignment:** a project that disappears from `projects` was unassigned by an admin. Phase-1 behaviour for the client: keep the local project but stop updating it (do not delete local data); surface it in the Admin panel as "no longer managed centrally".
  - **Templates are not delivered to PCs in Phase 1** (admin-side authoring aid only); the pull contract covers projects only.
  - **`X-Operator-Id`:** self-asserted, audit-only; send ASCII (percent-encode anything else) — the server treats header bytes as latin-1 and caps at 100 chars.
  - **Polling:** use `poll_interval_seconds` from the last response (30..86400, default 600); pull on startup and every interval; `heartbeat` is optional in Phase 1 (a successful pull already refreshes `last_seen_at`). No ETag/conditional GET: comparing versions client-side is enough.
  - **Config contents:** central stores/returns the FULL project config but rejects configs containing credential-like keys/values; so a pulled config has no secrets. Pulled configs must never carry SFTP passwords — those stay in the PC's local config.
- **Open decision for plan 2 (flag to the user before building it):** project configs contain PC-local sections (`delivery`, `export`, `scanner` — local paths, SFTP targets). Central stores the full config, but rejects credentials (Task 6). Plan 2's sync client should overlay the PC's own local `delivery`/`export`/`scanner` on top of pulled configs, mirroring `_LIVE_ONLY_TOP_KEYS` in `core/template_manager.py`.

## Review Focus

- A project/template config containing a credential (e.g. `delivery.sftp.password`) must be rejected with 422 and never stored — otherwise SFTP passwords land in the cloud DB. (Task 6)
- PC A's valid key calling PC B's endpoints must get 403, and a revoked or replaced key must get 401. (Task 5)
- Re-submitting an identical config must not bump the version, otherwise every PC reloads for nothing. (Task 6)
- An enrollment code that is reused, expired, or unknown must fail identically (400) and never mint a second key. (Task 5)
- An oversized or control-character-laden `X-Operator-Id` must not forge or bloat audit entries. (Tasks 2 and 8)

---

## File Structure

| File | Responsibility |
|---|---|
| `central/__init__.py` | Package marker |
| `central/requirements.txt` | Service-only dependencies (kept out of the exe's `requirements.txt`) |
| `central/settings.py` | `Settings` dataclass + `from_env()` |
| `central/models.py` | SQLAlchemy tables, `utcnow()` |
| `central/db.py` | Engine and session factory |
| `central/security.py` | Password/API-key/code primitives, log sanitising |
| `central/audit.py` | `record()` helper writing `audit_events` |
| `central/deps.py` | `get_db`, `require_admin`, `require_csrf`, `require_pc` |
| `central/config_rules.py` | Config size limit and credential scan |
| `central/versioning.py` | `save_version()` for projects/templates |
| `central/settings_store.py` | Poll-interval get/set |
| `central/routes_admin_auth.py` | `/admin/login`, `/admin/logout`, `/admin/me` |
| `central/routes_admin_pcs.py` | `/admin/pcs*` |
| `central/routes_admin_config.py` | `/admin/projects*`, `/admin/templates*`, push, settings |
| `central/routes_pcs.py` | `/pcs/register`, `/pcs/{id}/config`, `/pcs/{id}/heartbeat` |
| `central/app.py` | `create_app(settings)`, `create_app_from_env()` |
| `central/manage.py` | `create-admin` CLI |
| `central/README.md` | Deploy and operate |
| `tests/central/` | `conftest.py` plus one test module per task |

---

### Task 1: Scaffold, settings, models, app factory

**Files:**
- Create: `central/__init__.py`, `central/requirements.txt`, `central/settings.py`, `central/models.py`, `central/db.py`, `central/app.py`
- Create: `tests/central/__init__.py`, `tests/central/conftest.py`, `tests/central/test_app.py`

**Interfaces:**
- Produces: `Settings(database_url, session_secret, cookie_secure=True, enrollment_code_ttl_minutes=60, login_max_failures=5, login_lockout_minutes=15, default_poll_interval_seconds=600)`; `Settings.from_env()`; models `Pc, EnrollmentCode, AdminUser, Project, Template, ConfigVersion, PcAssignment, Setting, AuditEvent`; `utcnow() -> naive UTC datetime`; `make_engine(url)`, `make_session_factory(engine)`; `create_app(settings) -> FastAPI` with `app.state.settings/engine/session_factory`.

- [ ] **Step 1: Create the package files and install dependencies**

`central/__init__.py`:
```python
"""dokscanner-central: config/fleet service for DOKScanner.exe installs."""
```

`central/requirements.txt`:
```
fastapi>=0.115
uvicorn[standard]>=0.30
sqlalchemy>=2.0
psycopg[binary]>=3.2
itsdangerous>=2.2
httpx>=0.27
bcrypt>=4.1
jsonschema>=4.21
```

Run: `.\.venv\Scripts\python.exe -m pip install -r central/requirements.txt`
Expected: installs without error.

- [ ] **Step 2: Write the failing tests**

`tests/central/__init__.py`: empty file.

`tests/central/conftest.py`:
```python
"""Fixtures for the central-service tests. The whole directory is skipped
when central/requirements.txt is not installed, so the DOKScanner.exe test
run is unaffected."""
import importlib.util

import pytest

_DEPS = ("fastapi", "sqlalchemy", "httpx", "itsdangerous")
if not all(importlib.util.find_spec(m) for m in _DEPS):
    collect_ignore_glob = ["test_*.py"]
else:
    from fastapi.testclient import TestClient

    from central.app import create_app
    from central.settings import Settings

    @pytest.fixture()
    def settings(tmp_path):
        return Settings(
            database_url=f"sqlite:///{tmp_path / 'central.db'}",
            session_secret="x" * 40, cookie_secure=False)

    @pytest.fixture()
    def app(settings):
        return create_app(settings)

    @pytest.fixture()
    def client(app):
        with TestClient(app) as c:
            yield c
```

`tests/central/test_app.py`:
```python
import pytest
from sqlalchemy import inspect

from central.settings import Settings


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_all_tables_created(app):
    names = set(inspect(app.state.engine).get_table_names())
    assert {"pcs", "enrollment_codes", "admin_users", "projects",
            "project_templates", "config_versions", "pc_assignments",
            "settings", "audit_events"} <= names


def test_api_docs_are_not_exposed(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_settings_from_env_requires_strong_secret(monkeypatch):
    monkeypatch.setenv("CENTRAL_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("CENTRAL_SESSION_SECRET", "short")
    with pytest.raises(RuntimeError, match="at least 32"):
        Settings.from_env()


def test_settings_from_env_reads_values(monkeypatch):
    monkeypatch.setenv("CENTRAL_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("CENTRAL_SESSION_SECRET", "s" * 32)
    monkeypatch.setenv("CENTRAL_COOKIE_SECURE", "0")
    s = Settings.from_env()
    assert s.database_url == "sqlite://" and s.cookie_secure is False
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/central/test_app.py`
Expected: collection error / FAIL — `ModuleNotFoundError: No module named 'central.app'`.

- [ ] **Step 4: Implement settings, models, db, app**

`central/settings.py`:
```python
"""Service configuration, read from the environment (no secrets in code)."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    session_secret: str
    cookie_secure: bool = True
    enrollment_code_ttl_minutes: int = 60
    login_max_failures: int = 5
    login_lockout_minutes: int = 15
    default_poll_interval_seconds: int = 600

    @classmethod
    def from_env(cls) -> "Settings":
        secret = os.environ.get("CENTRAL_SESSION_SECRET", "")
        if len(secret) < 32:
            raise RuntimeError(
                "CENTRAL_SESSION_SECRET must be set to at least 32 characters")
        return cls(
            database_url=os.environ["CENTRAL_DATABASE_URL"],
            session_secret=secret,
            cookie_secure=os.environ.get("CENTRAL_COOKIE_SECURE", "1") != "0")
```

`central/models.py`:
```python
"""SQLAlchemy models for the central service (spec: Data model)."""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (Boolean, DateTime, ForeignKey, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Naive UTC: SQLite drops tzinfo, so every column stores naive UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Pc(Base):
    __tablename__ = "pcs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), unique=True)
    api_key_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    registered_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EnrollmentCode(Base):
    __tablename__ = "enrollment_codes"
    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    pc_id: Mapped[str] = mapped_column(ForeignKey("pcs.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class AdminUser(Base):
    __tablename__ = "admin_users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True,
                                    autoincrement=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20), default="admin")
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    current_version: Mapped[int] = mapped_column(Integer, default=0)


class Template(Base):
    __tablename__ = "project_templates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(100), default="")
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    built_in: Mapped[bool] = mapped_column(Boolean, default=False)
    current_version: Mapped[int] = mapped_column(Integer, default=0)


class ConfigVersion(Base):
    """Append-only history; enables the admin history view and rollback."""
    __tablename__ = "config_versions"
    __table_args__ = (UniqueConstraint("kind", "owner_id", "version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True,
                                    autoincrement=True)
    kind: Mapped[str] = mapped_column(String(10))  # "project" | "template"
    owner_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer)
    config_json: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PcAssignment(Base):
    __tablename__ = "pc_assignments"
    pc_id: Mapped[str] = mapped_column(ForeignKey("pcs.id"),
                                       primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"),
                                            primary_key=True)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True,
                                    autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    actor: Mapped[str] = mapped_column(String(150))
    action: Mapped[str] = mapped_column(String(100))
    detail: Mapped[str] = mapped_column(Text, default="{}")
```

`central/db.py`:
```python
"""Engine and session factory. Phase 1 creates tables with create_all();
adopt Alembic before the first schema change after go-live."""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from central.models import Base


def make_engine(database_url: str):
    if database_url.startswith("sqlite"):
        engine = create_engine(
            database_url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _enforce_fks(dbapi_conn, _record):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    else:
        engine = create_engine(database_url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    return engine


def make_session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)
```

`central/app.py`:
```python
"""FastAPI application factory."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from central.db import make_engine, make_session_factory
from central.settings import Settings


@asynccontextmanager
async def _lifespan(app: FastAPI):
    yield
    app.state.engine.dispose()


def create_app(settings: Settings) -> FastAPI:
    engine = make_engine(settings.database_url)
    # API docs are off: the surface is small and not worth advertising.
    app = FastAPI(title="dokscanner-central", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=_lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.add_middleware(
        SessionMiddleware, secret_key=settings.session_secret,
        session_cookie="dok_central", https_only=settings.cookie_secure,
        same_site="strict", max_age=8 * 3600)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/central/test_app.py`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add central tests/central
git commit -m "feat(central): scaffold service with settings, models and app factory"
```

---

### Task 2: Security primitives and audit helper

**Files:**
- Create: `central/security.py`, `central/audit.py`
- Test: `tests/central/test_security.py`

**Interfaces:**
- Consumes: `AuditEvent` from `central.models`.
- Produces: `hash_password(p) -> str`, `verify_password(p, hashed) -> bool`, `burn_password_check(p) -> bool` (always False; equalises timing for unknown users), `new_api_key() -> str` (`"dsk_"` + 43 chars), `hash_secret(s) -> str` (64-char SHA-256 hex), `new_enrollment_code() -> str`, `sanitize_log_value(v: str | None, limit=100) -> str`; `audit.record(db, actor: str, action: str, detail: dict | None = None) -> None` (adds, does not commit).

- [ ] **Step 1: Write the failing tests**

`tests/central/test_security.py`:
```python
import json

from central import security as sec
from central.audit import record
from central.models import AuditEvent


def test_password_roundtrip():
    h = sec.hash_password("pw one")
    assert sec.verify_password("pw one", h)
    assert not sec.verify_password("pw two", h)


def test_long_and_multibyte_passwords_work_and_stay_significant():
    long_pw = "a" * 150
    h = sec.hash_password(long_pw)
    assert sec.verify_password(long_pw, h)
    assert not sec.verify_password("a" * 149 + "b", h)  # tail is not truncated
    assert sec.verify_password("pässwörd-日本語-" * 10, sec.hash_password("pässwörd-日本語-" * 10))
    assert sec.burn_password_check("z" * 200) is False  # must not raise


def test_verify_password_with_garbage_hash_is_false_not_error():
    assert sec.verify_password("x", "not-a-bcrypt-hash") is False


def test_burn_password_check_is_always_false():
    assert sec.burn_password_check("whatever") is False


def test_api_keys_are_unique_and_prefixed():
    a, b = sec.new_api_key(), sec.new_api_key()
    assert a != b and a.startswith("dsk_") and len(a) >= 40


def test_hash_secret_is_deterministic_sha256_hex():
    assert sec.hash_secret("a") == sec.hash_secret("a")
    assert len(sec.hash_secret("a")) == 64
    assert sec.hash_secret("a") != sec.hash_secret("b")


def test_enrollment_codes_are_long_and_unique():
    a, b = sec.new_enrollment_code(), sec.new_enrollment_code()
    assert a != b and len(a) >= 16


def test_sanitize_log_value_strips_control_chars_and_truncates():
    assert sec.sanitize_log_value("op\nFORGED: x") == "op?FORGED: x"
    assert sec.sanitize_log_value("a\r\x00b\x7fc") == "a??b?c"
    assert len(sec.sanitize_log_value("x" * 500)) == 100
    assert sec.sanitize_log_value(None) == ""


def test_audit_record_adds_row_without_committing(app):
    with app.state.session_factory() as db:
        record(db, "admin:root", "pc_created", {"pc_id": "p1"})
        db.rollback()
        assert db.query(AuditEvent).count() == 0
        record(db, "admin:root", "pc_created", {"pc_id": "p1"})
        db.commit()
        row = db.query(AuditEvent).one()
        assert row.actor == "admin:root" and row.action == "pc_created"
        assert json.loads(row.detail) == {"pc_id": "p1"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/central/test_security.py`
Expected: FAIL — `ImportError: cannot import name 'security' from 'central'`.

- [ ] **Step 3: Implement**

`central/security.py`:
```python
"""Credential primitives. Passwords: bcrypt. API keys / enrollment codes are
high-entropy random values, so a plain SHA-256 at rest is sufficient and
allows an indexed lookup."""
from __future__ import annotations

import base64
import hashlib
import secrets

import bcrypt


def _prehash(password: str) -> bytes:
    """bcrypt 5 raises on inputs over 72 bytes (and older versions silently
    truncate). Pre-hashing to a fixed 44-byte value makes any password
    length safe and keeps every character significant."""
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prehash(password), hashed.encode())
    except ValueError:
        return False


_DUMMY_HASH = hash_password("timing-equaliser")


def burn_password_check(password: str) -> bool:
    """Spend the same time as a real check so an unknown username is not
    distinguishable from a wrong password by response time."""
    bcrypt.checkpw(_prehash(password), _DUMMY_HASH.encode())
    return False


def new_api_key() -> str:
    return "dsk_" + secrets.token_urlsafe(32)


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_enrollment_code() -> str:
    return secrets.token_urlsafe(12)


def sanitize_log_value(value: str | None, limit: int = 100) -> str:
    """Make client-supplied text safe to store in an audit entry: control
    characters (log forging) become '?', length is capped."""
    if not value:
        return ""
    cleaned = "".join("?" if ord(c) < 32 or ord(c) == 127 else c
                      for c in value)
    return cleaned[:limit]
```

`central/audit.py`:
```python
"""Server-side audit trail (adds to the session; the caller commits)."""
import json

from central.models import AuditEvent


def record(db, actor: str, action: str, detail: dict | None = None) -> None:
    db.add(AuditEvent(actor=actor, action=action,
                      detail=json.dumps(detail or {})))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/central/test_security.py`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add central/security.py central/audit.py tests/central/test_security.py
git commit -m "feat(central): credential primitives, log sanitising, audit helper"
```

---

### Task 3: Admin login, session, CSRF, lockout, create-admin CLI

**Files:**
- Create: `central/deps.py`, `central/routes_admin_auth.py`, `central/manage.py`
- Modify: `central/app.py` (include the router), `tests/central/conftest.py` (admin fixtures)
- Test: `tests/central/test_admin_auth.py`

**Interfaces:**
- Consumes: `AdminUser`, `verify_password`, `burn_password_check`, `Settings.login_max_failures/login_lockout_minutes`.
- Produces: `deps.get_db`, `deps.require_admin -> AdminUser`, `deps.require_csrf`; `POST /admin/login {username, password}` → `{username, role, csrf_token}`; `GET /admin/me`; `POST /admin/logout` (204); `manage.create_admin(session_factory, username, password, role="admin")`; fixtures `admin` and `admin_client` (logged in, `X-CSRF-Token` header preset).

- [ ] **Step 1: Add fixtures and write the failing tests**

Append to the `else:` block of `tests/central/conftest.py` (same indentation as the other fixtures):
```python
    from central.manage import create_admin

    @pytest.fixture()
    def admin(app):
        create_admin(app.state.session_factory, "root", "correct horse battery")

    @pytest.fixture()
    def admin_client(client, admin):
        r = client.post("/admin/login", json={
            "username": "root", "password": "correct horse battery"})
        assert r.status_code == 200
        client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        return client
```

`tests/central/test_admin_auth.py`:
```python
import pytest

from central.manage import create_admin

PW = "correct horse battery"


def _login(client, password=PW, username="root"):
    return client.post("/admin/login",
                       json={"username": username, "password": password})


def test_login_returns_csrf_token_and_me_works(client, admin):
    r = _login(client)
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "root" and len(body["csrf_token"]) >= 32
    me = client.get("/admin/me")
    assert me.status_code == 200 and me.json()["username"] == "root"


def test_me_requires_login(client):
    assert client.get("/admin/me").status_code == 401


def test_wrong_password_and_unknown_user_look_identical(client, admin):
    a = _login(client, "wrong password!")
    b = _login(client, "whatever", username="nobody")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_lockout_after_repeated_failures(client, admin):
    for _ in range(5):
        assert _login(client, "wrong password!").status_code == 401
    # even the correct password is refused while locked
    assert _login(client).status_code == 429


def test_successful_login_resets_failure_count(client, admin):
    for _ in range(4):
        _login(client, "wrong password!")
    assert _login(client).status_code == 200
    for _ in range(4):
        _login(client, "wrong password!")
    assert _login(client).status_code == 200  # never reached 5 in a row


def test_logout_requires_csrf_and_ends_session(client, admin):
    token = _login(client).json()["csrf_token"]
    assert client.post("/admin/logout").status_code == 403
    r = client.post("/admin/logout", headers={"X-CSRF-Token": token})
    assert r.status_code == 204
    assert client.get("/admin/me").status_code == 401


def test_wrong_csrf_token_rejected(client, admin):
    _login(client)
    r = client.post("/admin/logout", headers={"X-CSRF-Token": "nope"})
    assert r.status_code == 403


def test_create_admin_enforces_password_length_and_uniqueness(app):
    factory = app.state.session_factory
    with pytest.raises(ValueError, match="at least 12"):
        create_admin(factory, "short", "tooshort")
    create_admin(factory, "dup", PW)
    with pytest.raises(ValueError, match="already exists"):
        create_admin(factory, "dup", PW)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/central/test_admin_auth.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'central.manage'`.

- [ ] **Step 3: Implement**

`central/deps.py`:
```python
"""Request dependencies: DB session, admin session, CSRF, PC API key."""
from __future__ import annotations

import hmac
from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from central.models import AdminUser, Pc
from central.security import hash_secret

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


def require_admin(request: Request,
                  db: Session = Depends(get_db)) -> AdminUser:
    admin_id = request.session.get("admin_id")
    user = db.get(AdminUser, admin_id) if admin_id else None
    if user is None:
        raise HTTPException(401, "login required")
    return user


def require_csrf(request: Request) -> None:
    if request.method in SAFE_METHODS:
        return
    sent = request.headers.get("x-csrf-token", "").encode("utf-8")
    expected = str(request.session.get("csrf", "")).encode("utf-8")
    if not expected or not hmac.compare_digest(sent, expected):
        raise HTTPException(403, "missing or invalid CSRF token")


def require_pc(pc_id: str, request: Request,
               db: Session = Depends(get_db)) -> Pc:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "API key required")
    pc = db.scalar(select(Pc).where(
        Pc.api_key_hash == hash_secret(token.strip())))
    if pc is None:
        raise HTTPException(401, "invalid API key")
    if pc.id != pc_id:
        raise HTTPException(403, "API key does not belong to this PC")
    return pc
```

`central/routes_admin_auth.py`:
```python
"""Admin login/logout (session cookie + CSRF token)."""
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from central.deps import get_db, require_admin, require_csrf
from central.models import AdminUser, utcnow
from central.security import burn_password_check, verify_password

router = APIRouter(prefix="/admin", tags=["admin-auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    s = request.app.state.settings
    now = utcnow()
    user = db.scalar(select(AdminUser).where(
        AdminUser.username == body.username))
    if user and user.locked_until and user.locked_until > now:
        raise HTTPException(429, "too many failed attempts; try again later")
    ok = (verify_password(body.password, user.password_hash) if user
          else burn_password_check(body.password))
    if not ok:
        if user:
            user.failed_count += 1
            if user.failed_count >= s.login_max_failures:
                user.locked_until = now + timedelta(
                    minutes=s.login_lockout_minutes)
                user.failed_count = 0
            db.commit()
        raise HTTPException(401, "invalid credentials")
    user.failed_count = 0
    user.locked_until = None
    db.commit()
    request.session.clear()  # new session on every login (no fixation)
    request.session["admin_id"] = user.id
    request.session["csrf"] = secrets.token_urlsafe(32)
    return {"username": user.username, "role": user.role,
            "csrf_token": request.session["csrf"]}


@router.get("/me")
def me(user: AdminUser = Depends(require_admin)):
    return {"username": user.username, "role": user.role}


@router.post("/logout", status_code=204,
             dependencies=[Depends(require_admin), Depends(require_csrf)])
def logout(request: Request):
    request.session.clear()
    return Response(status_code=204)
```

`central/manage.py`:
```python
"""Operator CLI.  Usage: python -m central.manage create-admin USERNAME"""
from __future__ import annotations

import getpass
import sys

from sqlalchemy import select

from central.db import make_engine, make_session_factory
from central.models import AdminUser
from central.security import hash_password
from central.settings import Settings

MIN_PASSWORD_LEN = 12


def create_admin(session_factory, username: str, password: str,
                 role: str = "admin") -> None:
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(
            f"password must be at least {MIN_PASSWORD_LEN} characters")
    with session_factory() as db:
        if db.scalar(select(AdminUser).where(AdminUser.username == username)):
            raise ValueError(f"admin {username!r} already exists")
        db.add(AdminUser(username=username,
                         password_hash=hash_password(password), role=role))
        db.commit()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] != "create-admin":
        print("usage: python -m central.manage create-admin USERNAME",
              file=sys.stderr)
        return 2
    factory = make_session_factory(
        make_engine(Settings.from_env().database_url))
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat: "):
        print("passwords differ", file=sys.stderr)
        return 1
    try:
        create_admin(factory, argv[1], password)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"admin {argv[1]!r} created")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

In `central/app.py`, add after the middleware block and before `/healthz`:
```python
    from central import routes_admin_auth
    app.include_router(routes_admin_auth.router)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/central`
Expected: all pass (Task 1–3 tests).

- [ ] **Step 5: Commit**

```bash
git add central tests/central
git commit -m "feat(central): admin login with session, CSRF, lockout and create-admin CLI"
```

---

### Task 4: Admin PC management and enrollment codes

**Files:**
- Create: `central/routes_admin_pcs.py`
- Modify: `central/app.py` (include router)
- Test: `tests/central/test_admin_pcs.py`

**Interfaces:**
- Consumes: `require_admin`, `require_csrf`, `get_db`, `Pc`, `EnrollmentCode`, `PcAssignment`, `new_enrollment_code`, `hash_secret`, `audit.record`, `Settings.enrollment_code_ttl_minutes`.
- Produces: `POST /admin/pcs {display_name}` → 201 `{pc_id, display_name, enrollment_code, expires_at}`; `GET /admin/pcs` → list of `{id, display_name, registered, registered_at, last_seen_at, assigned_project_ids}`; `POST /admin/pcs/{pc_id}/enrollment-code` → `{pc_id, enrollment_code, expires_at}` (replaces any live code); `DELETE /admin/pcs/{pc_id}/key` → 204 (revokes API key). Helper `issue_code(db, pc, settings) -> (code, expires)` reused by tests.

- [ ] **Step 1: Write the failing tests**

`tests/central/test_admin_pcs.py`:
```python
from central.models import EnrollmentCode, Pc


def test_pc_endpoints_require_login(client):
    assert client.post("/admin/pcs", json={"display_name": "x"}).status_code == 401
    assert client.get("/admin/pcs").status_code == 401


def test_create_pc_returns_one_time_code_and_lists_unregistered(admin_client):
    r = admin_client.post("/admin/pcs", json={"display_name": "Scan-01"})
    assert r.status_code == 201
    body = r.json()
    assert body["display_name"] == "Scan-01"
    assert len(body["enrollment_code"]) >= 16 and body["pc_id"]
    assert "api_key" not in body

    rows = admin_client.get("/admin/pcs").json()
    assert [(p["display_name"], p["registered"]) for p in rows] == [
        ("Scan-01", False)]
    assert "enrollment_code" not in rows[0] and "api_key_hash" not in rows[0]


def test_duplicate_display_name_conflicts(admin_client):
    admin_client.post("/admin/pcs", json={"display_name": "Scan-01"})
    r = admin_client.post("/admin/pcs", json={"display_name": "Scan-01"})
    assert r.status_code == 409


def test_blank_display_name_rejected(admin_client):
    assert admin_client.post("/admin/pcs",
                             json={"display_name": ""}).status_code == 422


def test_new_enrollment_code_replaces_the_old_one(admin_client, app):
    d = admin_client.post("/admin/pcs", json={"display_name": "A"}).json()
    r = admin_client.post(f"/admin/pcs/{d['pc_id']}/enrollment-code")
    assert r.status_code == 200
    assert r.json()["enrollment_code"] != d["enrollment_code"]
    with app.state.session_factory() as db:
        assert db.query(EnrollmentCode).filter_by(pc_id=d["pc_id"]).count() == 1


def test_enrollment_code_for_unknown_pc_is_404(admin_client):
    assert admin_client.post("/admin/pcs/nope/enrollment-code").status_code == 404


def test_revoke_key_clears_hash_and_404s_for_unknown(admin_client, app):
    d = admin_client.post("/admin/pcs", json={"display_name": "A"}).json()
    with app.state.session_factory() as db:
        db.get(Pc, d["pc_id"]).api_key_hash = "h" * 64
        db.commit()
    assert admin_client.delete(f"/admin/pcs/{d['pc_id']}/key").status_code == 204
    with app.state.session_factory() as db:
        assert db.get(Pc, d["pc_id"]).api_key_hash is None
    assert admin_client.delete("/admin/pcs/nope/key").status_code == 404


def test_mutations_need_csrf(client, admin):
    client.post("/admin/login", json={
        "username": "root", "password": "correct horse battery"})
    r = client.post("/admin/pcs", json={"display_name": "x"})  # no CSRF header
    assert r.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/central/test_admin_pcs.py`
Expected: FAIL — 404 on `/admin/pcs` (route does not exist).

- [ ] **Step 3: Implement**

`central/routes_admin_pcs.py`:
```python
"""Admin: register PCs and manage their enrollment codes / API keys."""
from datetime import datetime, timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from central.audit import record
from central.deps import get_db, require_admin, require_csrf
from central.models import (AdminUser, EnrollmentCode, Pc, PcAssignment,
                            utcnow)
from central.security import hash_secret, new_enrollment_code

router = APIRouter(prefix="/admin", tags=["admin-pcs"],
                   dependencies=[Depends(require_admin),
                                 Depends(require_csrf)])


class PcIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def issue_code(db: Session, pc: Pc, settings) -> tuple[str, datetime]:
    """One live enrollment code per PC: issuing replaces any earlier one."""
    db.execute(delete(EnrollmentCode).where(EnrollmentCode.pc_id == pc.id))
    code = new_enrollment_code()
    expires = utcnow() + timedelta(minutes=settings.enrollment_code_ttl_minutes)
    db.add(EnrollmentCode(code_hash=hash_secret(code), pc_id=pc.id,
                          expires_at=expires))
    return code, expires


def _get_pc(db: Session, pc_id: str) -> Pc:
    pc = db.get(Pc, pc_id)
    if pc is None:
        raise HTTPException(404, "unknown PC")
    return pc


@router.post("/pcs", status_code=201)
def create_pc(body: PcIn, request: Request,
              admin: AdminUser = Depends(require_admin),
              db: Session = Depends(get_db)):
    if db.scalar(select(Pc).where(Pc.display_name == body.display_name)):
        raise HTTPException(409, "a PC with that name already exists")
    pc = Pc(id=str(uuid4()), display_name=body.display_name,
            created_at=utcnow())
    db.add(pc)
    db.flush()
    code, expires = issue_code(db, pc, request.app.state.settings)
    record(db, f"admin:{admin.username}", "pc_created", {"pc_id": pc.id})
    db.commit()
    return {"pc_id": pc.id, "display_name": pc.display_name,
            "enrollment_code": code, "expires_at": _iso(expires)}


@router.get("/pcs")
def list_pcs(db: Session = Depends(get_db)):
    assigned: dict[str, list[str]] = {}
    for a in db.scalars(select(PcAssignment)):
        assigned.setdefault(a.pc_id, []).append(a.project_id)
    return [{"id": p.id, "display_name": p.display_name,
             "registered": p.api_key_hash is not None,
             "registered_at": _iso(p.registered_at),
             "last_seen_at": _iso(p.last_seen_at),
             "assigned_project_ids": sorted(assigned.get(p.id, []))}
            for p in db.scalars(select(Pc).order_by(Pc.display_name))]


@router.post("/pcs/{pc_id}/enrollment-code")
def new_code(pc_id: str, request: Request,
             admin: AdminUser = Depends(require_admin),
             db: Session = Depends(get_db)):
    pc = _get_pc(db, pc_id)
    code, expires = issue_code(db, pc, request.app.state.settings)
    record(db, f"admin:{admin.username}", "enrollment_code_issued",
           {"pc_id": pc.id})
    db.commit()
    return {"pc_id": pc.id, "enrollment_code": code,
            "expires_at": _iso(expires)}


@router.delete("/pcs/{pc_id}/key", status_code=204)
def revoke_key(pc_id: str, admin: AdminUser = Depends(require_admin),
               db: Session = Depends(get_db)):
    pc = _get_pc(db, pc_id)
    pc.api_key_hash = None
    record(db, f"admin:{admin.username}", "pc_key_revoked", {"pc_id": pc.id})
    db.commit()
    return Response(status_code=204)
```

In `central/app.py`, extend the router block:
```python
    from central import routes_admin_auth, routes_admin_pcs
    app.include_router(routes_admin_auth.router)
    app.include_router(routes_admin_pcs.router)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/central`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add central tests/central
git commit -m "feat(central): admin PC management with one-time enrollment codes"
```

---

### Task 5: PC registration, API-key auth, heartbeat

**Files:**
- Create: `central/settings_store.py`, `central/routes_pcs.py`
- Modify: `central/app.py` (include router), `tests/central/conftest.py` (`enrolled` fixture)
- Test: `tests/central/test_pcs.py`

**Interfaces:**
- Consumes: `require_pc`, `EnrollmentCode`, `Pc`, `new_api_key`, `hash_secret`, `audit.record`, `Setting`.
- Produces: `settings_store.get_poll_interval(db, default: int) -> int`, `settings_store.set_poll_interval(db, seconds: int) -> None` (no commit); `POST /pcs/register {code}` → `{pc_id, api_key, poll_interval_seconds}`; `POST /pcs/{pc_id}/heartbeat` → 204; fixture `enrolled` → `{"pc_id", "api_key", "client", "code"}`; test helper `auth(enrolled) -> dict` header.

- [ ] **Step 1: Add the fixture and write the failing tests**

Append to the `else:` block of `tests/central/conftest.py`:
```python
    @pytest.fixture()
    def enrolled(admin_client, app):
        d = admin_client.post("/admin/pcs",
                              json={"display_name": "PC-1"}).json()
        pc_client = TestClient(app)  # not entered: no lifespan, no admin cookie
        reg = pc_client.post("/pcs/register",
                             json={"code": d["enrollment_code"]})
        assert reg.status_code == 200
        return {"pc_id": d["pc_id"], "api_key": reg.json()["api_key"],
                "client": pc_client, "code": d["enrollment_code"]}
```

`tests/central/test_pcs.py`:
```python
from datetime import timedelta

from central.models import EnrollmentCode, Pc, utcnow
from central.security import hash_secret


def auth(e, key=None):
    return {"Authorization": f"Bearer {key or e['api_key']}"}


def test_register_returns_key_and_poll_interval(enrolled, admin_client):
    assert enrolled["api_key"].startswith("dsk_")
    rows = admin_client.get("/admin/pcs").json()
    assert rows[0]["registered"] is True and rows[0]["last_seen_at"]


def test_register_response_has_poll_interval(admin_client, app):
    from fastapi.testclient import TestClient
    d = admin_client.post("/admin/pcs", json={"display_name": "P"}).json()
    r = TestClient(app).post("/pcs/register",
                             json={"code": d["enrollment_code"]})
    assert r.json()["poll_interval_seconds"] == 600


def test_code_is_single_use(enrolled):
    r = enrolled["client"].post("/pcs/register", json={"code": enrolled["code"]})
    assert r.status_code == 400


def test_unknown_code_is_rejected_like_a_used_one(enrolled):
    used = enrolled["client"].post("/pcs/register", json={"code": enrolled["code"]})
    bad = enrolled["client"].post("/pcs/register",
                                  json={"code": "does-not-exist-123"})
    assert bad.status_code == used.status_code == 400
    assert bad.json() == used.json()


def test_expired_code_is_rejected_and_mints_no_key(admin_client, app):
    from fastapi.testclient import TestClient
    d = admin_client.post("/admin/pcs", json={"display_name": "P"}).json()
    with app.state.session_factory() as db:
        row = db.get(EnrollmentCode, hash_secret(d["enrollment_code"]))
        row.expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
    r = TestClient(app).post("/pcs/register", json={"code": d["enrollment_code"]})
    assert r.status_code == 400
    with app.state.session_factory() as db:
        assert db.get(Pc, d["pc_id"]).api_key_hash is None


def test_heartbeat_updates_last_seen(enrolled, app):
    with app.state.session_factory() as db:
        db.get(Pc, enrolled["pc_id"]).last_seen_at = None
        db.commit()
    r = enrolled["client"].post(f"/pcs/{enrolled['pc_id']}/heartbeat",
                                headers=auth(enrolled))
    assert r.status_code == 204
    with app.state.session_factory() as db:
        assert db.get(Pc, enrolled["pc_id"]).last_seen_at is not None


def test_heartbeat_without_or_with_garbage_key_is_401(enrolled):
    url = f"/pcs/{enrolled['pc_id']}/heartbeat"
    assert enrolled["client"].post(url).status_code == 401
    assert enrolled["client"].post(
        url, headers=auth(enrolled, "dsk_garbage")).status_code == 401


def test_key_of_another_pc_is_403(enrolled, admin_client, app):
    from fastapi.testclient import TestClient
    other = admin_client.post("/admin/pcs", json={"display_name": "PC-2"}).json()
    reg = TestClient(app).post("/pcs/register",
                               json={"code": other["enrollment_code"]}).json()
    r = enrolled["client"].post(f"/pcs/{enrolled['pc_id']}/heartbeat",
                                headers=auth(enrolled, reg["api_key"]))
    assert r.status_code == 403


def test_revoked_key_is_401(enrolled, admin_client):
    assert admin_client.delete(
        f"/admin/pcs/{enrolled['pc_id']}/key").status_code == 204
    r = enrolled["client"].post(f"/pcs/{enrolled['pc_id']}/heartbeat",
                                headers=auth(enrolled))
    assert r.status_code == 401


def test_reenrolling_replaces_the_old_key(enrolled, admin_client):
    code = admin_client.post(
        f"/admin/pcs/{enrolled['pc_id']}/enrollment-code").json()["enrollment_code"]
    new_key = enrolled["client"].post(
        "/pcs/register", json={"code": code}).json()["api_key"]
    url = f"/pcs/{enrolled['pc_id']}/heartbeat"
    assert enrolled["client"].post(url, headers=auth(enrolled)).status_code == 401
    assert enrolled["client"].post(
        url, headers=auth(enrolled, new_key)).status_code == 204
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/central/test_pcs.py`
Expected: FAIL — `/pcs/register` returns 404 (fixture assertion fails).

- [ ] **Step 3: Implement**

`central/settings_store.py`:
```python
"""Admin-configurable global settings stored in the settings table."""
from sqlalchemy.orm import Session

from central.models import Setting

_POLL_KEY = "poll_interval_seconds"


def get_poll_interval(db: Session, default: int) -> int:
    row = db.get(Setting, _POLL_KEY)
    return int(row.value) if row else default


def set_poll_interval(db: Session, seconds: int) -> None:
    row = db.get(Setting, _POLL_KEY)
    if row:
        row.value = str(seconds)
    else:
        db.add(Setting(key=_POLL_KEY, value=str(seconds)))
```

`central/routes_pcs.py`:
```python
"""PC-facing endpoints: registration, heartbeat (config pull added in Task 8)."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from central.audit import record
from central.deps import get_db, require_pc
from central.models import EnrollmentCode, Pc, utcnow
from central.security import hash_secret, new_api_key
from central.settings_store import get_poll_interval

router = APIRouter(prefix="/pcs", tags=["pcs"])


class RegisterIn(BaseModel):
    code: str = Field(min_length=8, max_length=100)


@router.post("/register")
def register(body: RegisterIn, request: Request,
             db: Session = Depends(get_db)):
    now = utcnow()
    row = db.get(EnrollmentCode, hash_secret(body.code))
    # unknown, used and expired codes all fail the same way
    if row is None or row.used_at is not None or row.expires_at < now:
        raise HTTPException(400, "invalid or expired enrollment code")
    pc = db.get(Pc, row.pc_id)
    api_key = new_api_key()
    pc.api_key_hash = hash_secret(api_key)
    pc.registered_at = now
    pc.last_seen_at = now
    row.used_at = now
    record(db, f"pc:{pc.id}", "pc_registered", {})
    db.commit()
    settings = request.app.state.settings
    return {"pc_id": pc.id, "api_key": api_key,
            "poll_interval_seconds": get_poll_interval(
                db, settings.default_poll_interval_seconds)}


@router.post("/{pc_id}/heartbeat", status_code=204)
def heartbeat(pc: Pc = Depends(require_pc), db: Session = Depends(get_db)):
    """Phase 1 stub: only refreshes last_seen_at (richer payload is Phase 2)."""
    pc.last_seen_at = utcnow()
    db.commit()
    return Response(status_code=204)
```

In `central/app.py`, extend the router block:
```python
    from central import routes_admin_auth, routes_admin_pcs, routes_pcs
    app.include_router(routes_admin_auth.router)
    app.include_router(routes_admin_pcs.router)
    app.include_router(routes_pcs.router)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/central`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add central tests/central
git commit -m "feat(central): PC registration via one-time code, API-key auth, heartbeat"
```

---

### Task 6: Admin projects and templates (versioned, validated, credential-free)

**Files:**
- Create: `central/config_rules.py`, `central/versioning.py`, `central/routes_admin_config.py`
- Modify: `central/app.py` (include router), `tests/central/conftest.py` (`project_config` fixture)
- Test: `tests/central/test_config_rules.py`, `tests/central/test_admin_config.py`

**Interfaces:**
- Consumes: `Project`, `Template`, `ConfigVersion`, `ProfileManager.validate`, `ProjectConfigError` (from `core.profile_manager`), `audit.record`.
- Produces: `config_rules.find_credential_path(node) -> str | None`, `config_rules.check_config(config: dict) -> None` (raises `ValueError`), `MAX_CONFIG_BYTES = 262144`; `versioning.save_version(db, kind, row, config, actor) -> bool` (True if a new version was created); `PUT /admin/projects/{id} {name, config}` → `{id, name, version, changed}`; `GET /admin/projects`, `GET /admin/projects/{id}` (incl. `config`), `GET /admin/projects/{id}/versions`, `GET /admin/projects/{id}/versions/{n}`; `PUT /admin/templates/{id} {name, category, config}` → `{id, name, version, changed}`; `GET /admin/templates`, `GET /admin/templates/{id}`. Ids match `^[A-Za-z0-9-]{1,36}$`.

- [ ] **Step 1: Add the fixture and write the failing tests**

Append to the `else:` block of `tests/central/conftest.py`:
```python
    @pytest.fixture()
    def project_config(tmp_path):
        from core.profile_manager import ProfileManager
        return ProfileManager(tmp_path / "pm").create("Seed")
```

`tests/central/test_config_rules.py`:
```python
import pytest

from central.config_rules import (MAX_CONFIG_BYTES, check_config,
                                  find_credential_path)


def test_finds_nested_credential_keys_case_insensitively():
    cfg = {"delivery": {"sftp": {"host": "h", "Password": "x"}}}
    assert find_credential_path(cfg) == "delivery.sftp.Password"


def test_finds_credentials_inside_lists():
    assert find_credential_path({"a": [{"api_key": "k"}]}) == "a[0].api_key"


def test_clean_config_has_no_credential_path():
    assert find_credential_path({"ocr": {"lang": "eng"}, "key_file": "C:/k"}) is None


def test_check_config_rejects_credentials_and_oversize():
    with pytest.raises(ValueError, match="credential"):
        check_config({"sftp": {"password": "x"}})
    with pytest.raises(ValueError, match="too large"):
        check_config({"blob": "x" * (MAX_CONFIG_BYTES + 1)})
    check_config({"ok": True})
```

`tests/central/test_admin_config.py`:
```python
import copy
import uuid


def pid():
    return str(uuid.uuid4())


def put_project(c, project_id, config, name="Invoices"):
    return c.put(f"/admin/projects/{project_id}",
                 json={"name": name, "config": config})


def test_new_project_is_version_1_and_listed(admin_client, project_config):
    p = pid()
    r = put_project(admin_client, p, project_config)
    assert r.status_code == 200
    assert r.json() == {"id": p, "name": "Invoices", "version": 1, "changed": True}
    assert admin_client.get("/admin/projects").json() == [
        {"id": p, "name": "Invoices", "version": 1}]
    got = admin_client.get(f"/admin/projects/{p}").json()
    assert got["config"]["project_id"] == p and got["config"]["name"] == "Invoices"


def test_changed_config_bumps_version_and_keeps_history(admin_client, project_config):
    p = pid()
    put_project(admin_client, p, project_config)
    changed = copy.deepcopy(project_config)
    changed["ocr"]["lang"] = "deu"
    r = put_project(admin_client, p, changed)
    assert r.json()["version"] == 2 and r.json()["changed"] is True
    versions = admin_client.get(f"/admin/projects/{p}/versions").json()
    assert [v["version"] for v in versions] == [2, 1]
    old = admin_client.get(f"/admin/projects/{p}/versions/1").json()
    assert old["config"]["ocr"]["lang"] == project_config["ocr"]["lang"]


def test_identical_resubmit_does_not_bump_version(admin_client, project_config):
    p = pid()
    put_project(admin_client, p, project_config)
    r = put_project(admin_client, p, project_config)
    assert r.json()["version"] == 1 and r.json()["changed"] is False
    assert len(admin_client.get(f"/admin/projects/{p}/versions").json()) == 1


def test_body_name_and_path_id_override_config(admin_client, project_config):
    p = pid()
    cfg = {**project_config, "project_id": "someone-else", "name": "Wrong"}
    r = put_project(admin_client, p, cfg, name="Right")
    got = admin_client.get(f"/admin/projects/{p}").json()
    assert r.status_code == 200
    assert got["config"]["project_id"] == p and got["config"]["name"] == "Right"


def test_invalid_project_config_is_422(admin_client, project_config):
    bad = {k: v for k, v in project_config.items() if k != "scanner"}
    r = put_project(admin_client, pid(), bad)
    assert r.status_code == 422


def test_credentials_in_config_are_rejected_and_not_stored(admin_client, project_config):
    p = pid()
    bad = copy.deepcopy(project_config)
    bad["delivery"]["sftp"] = {"enabled": True, "host": "h", "password": "hunter2"}
    r = put_project(admin_client, p, bad)
    assert r.status_code == 422 and "delivery.sftp.password" in r.json()["detail"]
    assert admin_client.get(f"/admin/projects/{p}").status_code == 404


def test_oversized_config_is_422(admin_client, project_config):
    big = {**project_config, "notes": "x" * 300_000}
    assert put_project(admin_client, pid(), big).status_code == 422


def test_malformed_project_id_is_422(admin_client, project_config):
    assert put_project(admin_client, "bad id!", project_config).status_code == 422


def test_unknown_project_and_version_are_404(admin_client, project_config):
    assert admin_client.get("/admin/projects/nope").status_code == 404
    p = pid()
    put_project(admin_client, p, project_config)
    assert admin_client.get(f"/admin/projects/{p}/versions/9").status_code == 404


def test_project_writes_need_login_and_csrf(client, admin, project_config):
    assert put_project(client, pid(), project_config).status_code == 401
    client.post("/admin/login", json={
        "username": "root", "password": "correct horse battery"})
    assert put_project(client, pid(), project_config).status_code == 403


def test_templates_roundtrip_and_never_built_in(admin_client):
    t = pid()
    r = admin_client.put(f"/admin/templates/{t}", json={
        "name": "Banking", "category": "banking",
        "config": {"ocr": {"zones": []}}, "built_in": True})
    assert r.status_code == 200 and r.json()["version"] == 1
    listed = admin_client.get("/admin/templates").json()
    assert listed == [{"id": t, "name": "Banking", "category": "banking",
                       "built_in": False, "version": 1}]
    assert admin_client.get(f"/admin/templates/{t}").json()["config"] == {
        "ocr": {"zones": []}}


def test_template_with_credentials_is_rejected(admin_client):
    r = admin_client.put(f"/admin/templates/{pid()}", json={
        "name": "T", "category": "x", "config": {"token": "abc"}})
    assert r.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/central/test_config_rules.py tests/central/test_admin_config.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'central.config_rules'`.

- [ ] **Step 3: Implement**

`central/config_rules.py`:
```python
"""Guard rails for config blobs stored centrally: size cap and a
credential scan so secrets (e.g. SFTP passwords) never reach the cloud DB."""
from __future__ import annotations

import json

MAX_CONFIG_BYTES = 256 * 1024
_CREDENTIAL_KEYS = {"password", "passwd", "secret", "token", "api_key",
                    "private_key"}


def find_credential_path(node, path: str = "") -> str | None:
    """Dotted path of the first credential-looking key, else None."""
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if str(key).lower() in _CREDENTIAL_KEYS:
                return here
            found = find_credential_path(value, here)
            if found:
                return found
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found = find_credential_path(value, f"{path}[{i}]")
            if found:
                return found
    return None


def check_config(config: dict) -> None:
    if len(json.dumps(config)) > MAX_CONFIG_BYTES:
        raise ValueError(f"config too large (limit {MAX_CONFIG_BYTES} bytes)")
    bad = find_credential_path(config)
    if bad:
        raise ValueError(
            f"config must not contain credentials (found {bad}); keep "
            "secrets on the PC")
```

`central/versioning.py`:
```python
"""Append-only config versioning shared by projects and templates."""
import json

from central.models import ConfigVersion


def save_version(db, kind: str, row, config: dict, actor: str) -> bool:
    """Store config on `row` (a Project or Template). Creates a new version
    only if the canonical JSON differs; returns whether it did."""
    text = json.dumps(config, sort_keys=True, separators=(",", ":"))
    if row.current_version > 0 and row.config_json == text:
        return False
    row.config_json = text
    row.current_version += 1
    db.add(ConfigVersion(kind=kind, owner_id=row.id,
                         version=row.current_version, config_json=text,
                         created_by=actor))
    return True
```

`central/routes_admin_config.py`:
```python
"""Admin: versioned project/template config (push and settings: Task 7)."""
import json

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from central.audit import record
from central.config_rules import check_config
from central.deps import get_db, require_admin, require_csrf
from central.models import (AdminUser, ConfigVersion, Project, Template)
from central.versioning import save_version
from core.profile_manager import ProfileManager, ProjectConfigError

router = APIRouter(prefix="/admin", tags=["admin-config"],
                   dependencies=[Depends(require_admin),
                                 Depends(require_csrf)])

_ID = Path(pattern=r"^[A-Za-z0-9-]{1,36}$")


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    config: dict


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(default="", max_length=100)
    config: dict


def _check(config: dict) -> None:
    try:
        check_config(config)
    except ValueError as e:
        raise HTTPException(422, str(e))


def _history(db: Session, kind: str, owner_id: str):
    return list(db.scalars(
        select(ConfigVersion).where(ConfigVersion.kind == kind,
                                    ConfigVersion.owner_id == owner_id)
        .order_by(ConfigVersion.version.desc())))


# -- projects ---------------------------------------------------------------
@router.put("/projects/{project_id}")
def put_project(body: ProjectIn, project_id: str = _ID,
                admin: AdminUser = Depends(require_admin),
                db: Session = Depends(get_db)):
    config = {**body.config, "project_id": project_id, "name": body.name}
    _check(config)
    try:
        ProfileManager.validate(config)
    except ProjectConfigError as e:
        raise HTTPException(422, str(e))
    row = db.get(Project, project_id)
    if row is None:
        row = Project(id=project_id, name=body.name, config_json="{}",
                      current_version=0)
        db.add(row)
    row.name = body.name
    changed = save_version(db, "project", row, config,
                           f"admin:{admin.username}")
    if changed:
        record(db, f"admin:{admin.username}", "project_saved",
               {"project_id": project_id, "version": row.current_version})
    db.commit()
    return {"id": row.id, "name": row.name, "version": row.current_version,
            "changed": changed}


@router.get("/projects")
def list_projects(db: Session = Depends(get_db)):
    return [{"id": p.id, "name": p.name, "version": p.current_version}
            for p in db.scalars(select(Project).order_by(Project.name))]


def _project(db: Session, project_id: str) -> Project:
    row = db.get(Project, project_id)
    if row is None:
        raise HTTPException(404, "unknown project")
    return row


@router.get("/projects/{project_id}")
def get_project(project_id: str = _ID, db: Session = Depends(get_db)):
    p = _project(db, project_id)
    return {"id": p.id, "name": p.name, "version": p.current_version,
            "config": json.loads(p.config_json)}


@router.get("/projects/{project_id}/versions")
def project_versions(project_id: str = _ID, db: Session = Depends(get_db)):
    _project(db, project_id)
    return [{"version": v.version, "created_by": v.created_by,
             "created_at": v.created_at.isoformat() + "Z"}
            for v in _history(db, "project", project_id)]


@router.get("/projects/{project_id}/versions/{version}")
def project_version(version: int, project_id: str = _ID,
                    db: Session = Depends(get_db)):
    _project(db, project_id)
    for v in _history(db, "project", project_id):
        if v.version == version:
            return {"version": v.version, "config": json.loads(v.config_json)}
    raise HTTPException(404, "unknown version")


# -- templates --------------------------------------------------------------
@router.put("/templates/{template_id}")
def put_template(body: TemplateIn, template_id: str = _ID,
                 admin: AdminUser = Depends(require_admin),
                 db: Session = Depends(get_db)):
    _check(body.config)
    row = db.get(Template, template_id)
    if row is None:
        row = Template(id=template_id, name=body.name, category=body.category,
                       config_json="{}", built_in=False, current_version=0)
        db.add(row)
    row.name, row.category = body.name, body.category
    changed = save_version(db, "template", row, body.config,
                           f"admin:{admin.username}")
    if changed:
        record(db, f"admin:{admin.username}", "template_saved",
               {"template_id": template_id, "version": row.current_version})
    db.commit()
    return {"id": row.id, "name": row.name, "version": row.current_version,
            "changed": changed}


@router.get("/templates")
def list_templates(db: Session = Depends(get_db)):
    return [{"id": t.id, "name": t.name, "category": t.category,
             "built_in": t.built_in, "version": t.current_version}
            for t in db.scalars(select(Template).order_by(Template.name))]


@router.get("/templates/{template_id}")
def get_template(template_id: str = _ID, db: Session = Depends(get_db)):
    t = db.get(Template, template_id)
    if t is None:
        raise HTTPException(404, "unknown template")
    return {"id": t.id, "name": t.name, "category": t.category,
            "built_in": t.built_in, "version": t.current_version,
            "config": json.loads(t.config_json)}
```

In `central/app.py`, extend the router block:
```python
    from central import (routes_admin_auth, routes_admin_config,
                         routes_admin_pcs, routes_pcs)
    app.include_router(routes_admin_auth.router)
    app.include_router(routes_admin_pcs.router)
    app.include_router(routes_admin_config.router)
    app.include_router(routes_pcs.router)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/central`
Expected: all pass. If `test_invalid_project_config_is_422` fails because the schema does not require `scanner`, change the test to delete a key that `core/project_schema.json` lists under `required`.

- [ ] **Step 5: Commit**

```bash
git add central tests/central
git commit -m "feat(central): versioned project/template config with credential and size guards"
```

---

### Task 7: Push/assign projects to PCs and the poll-interval setting

**Files:**
- Modify: `central/routes_admin_config.py` (add push, unassign, settings routes), `tests/central/conftest.py` (`project_id` helper fixture not needed — reuse Task 6 helpers)
- Test: `tests/central/test_push_settings.py`

**Interfaces:**
- Consumes: `PcAssignment`, `Pc`, `Project`, `settings_store`.
- Produces: `POST /admin/projects/{id}/push {pc_ids: [str], all_pcs: bool}` → `{assigned: [pc_id]}` (exactly one mode; idempotent; only PCs that exist now — a PC enrolled later gets nothing until pushed); `DELETE /admin/projects/{id}/assignments/{pc_id}` → 204; `GET /admin/settings` → `{poll_interval_seconds}`; `PUT /admin/settings {poll_interval_seconds: 30..86400}`.

- [ ] **Step 1: Write the failing tests**

`tests/central/test_push_settings.py`:
```python
import uuid


def _project(c, project_config):
    p = str(uuid.uuid4())
    r = c.put(f"/admin/projects/{p}",
              json={"name": "P", "config": project_config})
    assert r.status_code == 200
    return p


def _pc(c, name):
    return c.post("/admin/pcs", json={"display_name": name}).json()["pc_id"]


def _assigned(c):
    return {p["id"]: p["assigned_project_ids"]
            for p in c.get("/admin/pcs").json()}


def test_push_to_selected_pcs(admin_client, project_config):
    p, a, b = _project(admin_client, project_config), _pc(admin_client, "A"), _pc(admin_client, "B")
    r = admin_client.post(f"/admin/projects/{p}/push", json={"pc_ids": [a]})
    assert r.status_code == 200 and r.json() == {"assigned": [a]}
    assert _assigned(admin_client) == {a: [p], b: []}


def test_push_to_all_pcs_is_idempotent(admin_client, project_config):
    p, a, b = _project(admin_client, project_config), _pc(admin_client, "A"), _pc(admin_client, "B")
    for _ in range(2):
        r = admin_client.post(f"/admin/projects/{p}/push", json={"all_pcs": True})
        assert r.status_code == 200 and sorted(r.json()["assigned"]) == sorted([a, b])
    assert _assigned(admin_client) == {a: [p], b: [p]}


def test_pc_enrolled_after_push_to_all_gets_nothing(admin_client, project_config):
    p = _project(admin_client, project_config)
    _pc(admin_client, "A")
    admin_client.post(f"/admin/projects/{p}/push", json={"all_pcs": True})
    late = _pc(admin_client, "Late")
    assert _assigned(admin_client)[late] == []


def test_push_needs_exactly_one_mode(admin_client, project_config):
    p, a = _project(admin_client, project_config), _pc(admin_client, "A")
    url = f"/admin/projects/{p}/push"
    assert admin_client.post(url, json={}).status_code == 422
    assert admin_client.post(url, json={"pc_ids": [a], "all_pcs": True}).status_code == 422


def test_push_with_no_pcs_registered_is_an_empty_success(admin_client, project_config):
    p = _project(admin_client, project_config)
    r = admin_client.post(f"/admin/projects/{p}/push", json={"all_pcs": True})
    assert r.status_code == 200 and r.json() == {"assigned": []}


def test_push_unknown_pc_or_project_is_404(admin_client, project_config):
    p, a = _project(admin_client, project_config), _pc(admin_client, "A")
    assert admin_client.post(f"/admin/projects/{p}/push",
                             json={"pc_ids": [a, "ghost"]}).status_code == 404
    assert _assigned(admin_client)[a] == []  # nothing half-applied
    assert admin_client.post("/admin/projects/nope/push",
                             json={"all_pcs": True}).status_code == 404


def test_unassign(admin_client, project_config):
    p, a = _project(admin_client, project_config), _pc(admin_client, "A")
    admin_client.post(f"/admin/projects/{p}/push", json={"pc_ids": [a]})
    assert admin_client.delete(f"/admin/projects/{p}/assignments/{a}").status_code == 204
    assert _assigned(admin_client)[a] == []
    assert admin_client.delete(f"/admin/projects/{p}/assignments/{a}").status_code == 404


def test_poll_interval_setting(admin_client):
    assert admin_client.get("/admin/settings").json() == {"poll_interval_seconds": 600}
    assert admin_client.put("/admin/settings",
                            json={"poll_interval_seconds": 120}).status_code == 200
    assert admin_client.get("/admin/settings").json()["poll_interval_seconds"] == 120
    assert admin_client.put("/admin/settings",
                            json={"poll_interval_seconds": 5}).status_code == 422
    assert admin_client.put("/admin/settings",
                            json={"poll_interval_seconds": 10**7}).status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/central/test_push_settings.py`
Expected: FAIL — push/settings routes return 404/405.

- [ ] **Step 3: Implement**

Add imports at the top of `central/routes_admin_config.py` (merge with existing import lines):
```python
from fastapi import Request, Response
from sqlalchemy import delete

from central.models import Pc, PcAssignment
from central.settings_store import get_poll_interval, set_poll_interval
```

Append to `central/routes_admin_config.py`:
```python
# -- push / assignments -------------------------------------------------------
class PushIn(BaseModel):
    pc_ids: list[str] = Field(default_factory=list)
    all_pcs: bool = False


@router.post("/projects/{project_id}/push")
def push_project(body: PushIn, project_id: str = _ID,
                 admin: AdminUser = Depends(require_admin),
                 db: Session = Depends(get_db)):
    if body.all_pcs == bool(body.pc_ids):
        raise HTTPException(422, "give either pc_ids or all_pcs=true, not both/neither")
    _project(db, project_id)
    if body.all_pcs:
        target = list(db.scalars(select(Pc.id)))
    else:
        target = list(dict.fromkeys(body.pc_ids))
        known = set(db.scalars(select(Pc.id).where(Pc.id.in_(target))))
        missing = [p for p in target if p not in known]
        if missing:
            raise HTTPException(404, f"unknown PC(s): {', '.join(missing)}")
    have = set(db.scalars(select(PcAssignment.pc_id).where(
        PcAssignment.project_id == project_id)))
    for pc_id in target:
        if pc_id not in have:
            db.add(PcAssignment(pc_id=pc_id, project_id=project_id))
    record(db, f"admin:{admin.username}", "project_pushed",
           {"project_id": project_id, "pc_ids": target})
    db.commit()
    return {"assigned": target}


@router.delete("/projects/{project_id}/assignments/{pc_id}", status_code=204)
def unassign(pc_id: str, project_id: str = _ID,
             admin: AdminUser = Depends(require_admin),
             db: Session = Depends(get_db)):
    result = db.execute(delete(PcAssignment).where(
        PcAssignment.project_id == project_id, PcAssignment.pc_id == pc_id))
    if result.rowcount == 0:
        raise HTTPException(404, "no such assignment")
    record(db, f"admin:{admin.username}", "project_unassigned",
           {"project_id": project_id, "pc_id": pc_id})
    db.commit()
    return Response(status_code=204)


# -- global settings ------------------------------------------------------------
class SettingsIn(BaseModel):
    poll_interval_seconds: int = Field(ge=30, le=86400)


@router.get("/settings")
def get_settings(request: Request, db: Session = Depends(get_db)):
    default = request.app.state.settings.default_poll_interval_seconds
    return {"poll_interval_seconds": get_poll_interval(db, default)}


@router.put("/settings")
def put_settings(body: SettingsIn,
                 admin: AdminUser = Depends(require_admin),
                 db: Session = Depends(get_db)):
    set_poll_interval(db, body.poll_interval_seconds)
    record(db, f"admin:{admin.username}", "settings_changed",
           {"poll_interval_seconds": body.poll_interval_seconds})
    db.commit()
    return {"poll_interval_seconds": body.poll_interval_seconds}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/central`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add central tests/central
git commit -m "feat(central): push projects to PCs, unassign, global poll-interval setting"
```

---

### Task 8: PC config pull

**Files:**
- Modify: `central/routes_pcs.py`
- Test: `tests/central/test_config_pull.py`

**Interfaces:**
- Consumes: `require_pc`, `Project`, `PcAssignment`, `get_poll_interval`, `sanitize_log_value`, `audit.record`.
- Produces: `GET /pcs/{pc_id}/config` with Bearer key and optional `X-Operator-Id` → `{pc_id, poll_interval_seconds, projects: [{project_id, name, version, config}]}` (only that PC's assigned projects, sorted by name); refreshes `last_seen_at`; writes a `config_pull` audit event with the sanitized operator id and the served `{project_id: version}` map.

- [ ] **Step 1: Write the failing tests**

`tests/central/test_config_pull.py`:
```python
import copy
import json
import uuid

from central.models import AuditEvent, Pc
from tests.central.test_pcs import auth


def _publish(admin_client, project_config, name="Invoices"):
    p = str(uuid.uuid4())
    admin_client.put(f"/admin/projects/{p}",
                     json={"name": name, "config": project_config})
    return p


def _pull(e, **headers):
    return e["client"].get(f"/pcs/{e['pc_id']}/config",
                           headers={**auth(e), **headers})


def test_pull_with_nothing_assigned_is_empty(enrolled):
    r = _pull(enrolled)
    assert r.status_code == 200
    assert r.json() == {"pc_id": enrolled["pc_id"],
                        "poll_interval_seconds": 600, "projects": []}


def test_pull_returns_assigned_project_with_version(enrolled, admin_client, project_config):
    p = _publish(admin_client, project_config)
    admin_client.post(f"/admin/projects/{p}/push", json={"pc_ids": [enrolled["pc_id"]]})
    body = _pull(enrolled).json()
    assert len(body["projects"]) == 1
    proj = body["projects"][0]
    assert proj["project_id"] == p and proj["version"] == 1 and proj["name"] == "Invoices"
    assert proj["config"]["project_id"] == p


def test_pull_sees_new_version_after_admin_edit(enrolled, admin_client, project_config):
    p = _publish(admin_client, project_config)
    admin_client.post(f"/admin/projects/{p}/push", json={"pc_ids": [enrolled["pc_id"]]})
    edited = copy.deepcopy(project_config)
    edited["ocr"]["lang"] = "deu"
    admin_client.put(f"/admin/projects/{p}", json={"name": "Invoices", "config": edited})
    proj = _pull(enrolled).json()["projects"][0]
    assert proj["version"] == 2 and proj["config"]["ocr"]["lang"] == "deu"


def test_pull_honours_poll_interval_setting(enrolled, admin_client):
    admin_client.put("/admin/settings", json={"poll_interval_seconds": 90})
    assert _pull(enrolled).json()["poll_interval_seconds"] == 90


def test_pc_never_sees_projects_assigned_to_other_pcs(enrolled, admin_client, project_config, app):
    from fastapi.testclient import TestClient
    other = admin_client.post("/admin/pcs", json={"display_name": "PC-2"}).json()
    TestClient(app).post("/pcs/register", json={"code": other["enrollment_code"]})
    p = _publish(admin_client, project_config)
    admin_client.post(f"/admin/projects/{p}/push", json={"pc_ids": [other["pc_id"]]})
    assert _pull(enrolled).json()["projects"] == []


def test_pull_needs_valid_key_for_that_pc(enrolled, admin_client):
    url = f"/pcs/{enrolled['pc_id']}/config"
    assert enrolled["client"].get(url).status_code == 401
    assert enrolled["client"].get(url, headers=auth(enrolled, "dsk_bad")).status_code == 401
    admin_client.delete(f"/admin/pcs/{enrolled['pc_id']}/key")
    assert enrolled["client"].get(url, headers=auth(enrolled)).status_code == 401


def test_pull_refreshes_last_seen(enrolled, app):
    with app.state.session_factory() as db:
        db.get(Pc, enrolled["pc_id"]).last_seen_at = None
        db.commit()
    _pull(enrolled)
    with app.state.session_factory() as db:
        assert db.get(Pc, enrolled["pc_id"]).last_seen_at is not None


def test_operator_header_is_audited_and_capped(enrolled, app):
    _pull(enrolled, **{"X-Operator-Id": "alice"})
    _pull(enrolled, **{"X-Operator-Id": "x" * 500})
    with app.state.session_factory() as db:
        rows = [json.loads(r.detail) for r in
                db.query(AuditEvent).filter_by(action="config_pull").order_by(AuditEvent.id)]
    assert rows[0]["operator"] == "alice"
    assert len(rows[1]["operator"]) == 100
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/central/test_config_pull.py`
Expected: FAIL — `GET /pcs/{id}/config` returns 405/404.

- [ ] **Step 3: Implement**

Add imports at the top of `central/routes_pcs.py` (merge with existing):
```python
import json
from typing import Optional

from fastapi import Header
from sqlalchemy import select

from central.models import PcAssignment, Project
from central.security import sanitize_log_value
```

Append to `central/routes_pcs.py`:
```python
@router.get("/{pc_id}/config")
def get_config(request: Request, pc: Pc = Depends(require_pc),
               db: Session = Depends(get_db),
               x_operator_id: Optional[str] = Header(default=None)):
    """The sync client polls this. X-Operator-Id is audit-only: the operator
    does not authenticate to the server in Phase 1."""
    projects = list(db.scalars(
        select(Project).join(PcAssignment,
                             PcAssignment.project_id == Project.id)
        .where(PcAssignment.pc_id == pc.id).order_by(Project.name)))
    pc.last_seen_at = utcnow()
    record(db, f"pc:{pc.id}", "config_pull",
           {"operator": sanitize_log_value(x_operator_id),
            "projects": {p.id: p.current_version for p in projects}})
    db.commit()
    default = request.app.state.settings.default_poll_interval_seconds
    return {"pc_id": pc.id,
            "poll_interval_seconds": get_poll_interval(db, default),
            "projects": [{"project_id": p.id, "name": p.name,
                          "version": p.current_version,
                          "config": json.loads(p.config_json)}
                         for p in projects]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/central`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add central tests/central
git commit -m "feat(central): PC config pull endpoint with operator audit"
```

---

### Task 9: End-to-end round trip, env-based startup, deploy README

**Files:**
- Modify: `central/app.py` (add `create_app_from_env`)
- Create: `central/README.md`
- Test: `tests/central/test_roundtrip.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `create_app_from_env() -> FastAPI` (uvicorn factory: `uvicorn central.app:create_app_from_env --factory`).

- [ ] **Step 1: Write the failing tests**

`tests/central/test_roundtrip.py`:
```python
import copy
import uuid

from fastapi.testclient import TestClient

from central.app import create_app_from_env


def test_register_push_pull_update_revoke_roundtrip(admin_client, project_config, app):
    # admin enrols a PC; the PC exchanges the one-time code for its key
    pc = admin_client.post("/admin/pcs", json={"display_name": "Counter-1"}).json()
    pc_client = TestClient(app)
    key = pc_client.post("/pcs/register",
                         json={"code": pc["enrollment_code"]}).json()["api_key"]
    headers = {"Authorization": f"Bearer {key}", "X-Operator-Id": "alice"}
    url = f"/pcs/{pc['pc_id']}/config"
    assert pc_client.get(url, headers=headers).json()["projects"] == []

    # admin publishes and pushes a project; the PC sees v1
    p = str(uuid.uuid4())
    admin_client.put(f"/admin/projects/{p}",
                     json={"name": "Invoices", "config": project_config})
    admin_client.post(f"/admin/projects/{p}/push", json={"all_pcs": True})
    got = pc_client.get(url, headers=headers).json()["projects"][0]
    assert got["version"] == 1 and got["config"]["name"] == "Invoices"

    # admin edits; the PC sees v2; an identical re-save changes nothing
    edited = copy.deepcopy(project_config)
    edited["qc"]["pass_score"] = 90
    admin_client.put(f"/admin/projects/{p}", json={"name": "Invoices", "config": edited})
    admin_client.put(f"/admin/projects/{p}", json={"name": "Invoices", "config": edited})
    got = pc_client.get(url, headers=headers).json()["projects"][0]
    assert got["version"] == 2 and got["config"]["qc"]["pass_score"] == 90

    # admin revokes the key; the PC is locked out
    admin_client.delete(f"/admin/pcs/{pc['pc_id']}/key")
    assert pc_client.get(url, headers=headers).status_code == 401


def test_create_app_from_env_serves_healthz(monkeypatch, tmp_path):
    monkeypatch.setenv("CENTRAL_DATABASE_URL", f"sqlite:///{tmp_path / 'env.db'}")
    monkeypatch.setenv("CENTRAL_SESSION_SECRET", "e" * 40)
    with TestClient(create_app_from_env()) as c:
        assert c.get("/healthz").json() == {"status": "ok"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/central/test_roundtrip.py`
Expected: FAIL — `ImportError: cannot import name 'create_app_from_env'`.

- [ ] **Step 3: Implement**

Append to `central/app.py`:
```python
def create_app_from_env() -> FastAPI:
    """uvicorn factory entry point (reads CENTRAL_* environment variables)."""
    return create_app(Settings.from_env())
```

`central/README.md`:
````markdown
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

Run behind a reverse proxy that terminates TLS (HTTPS only). Do not expose
uvicorn directly.

## Enrolling a PC

1. Dashboard/API: `POST /admin/pcs {"display_name": "..."}` returns a one-time
   `enrollment_code` (valid 60 minutes).
2. On the PC, the installer/Admin panel calls `POST /pcs/register {"code": ...}`
   and stores the returned `api_key` in the DPAPI keystore.
3. Lost or compromised PC: `DELETE /admin/pcs/{id}/key`, then issue a new code.

## Operational notes

- Tables are created with `create_all()`; adopt Alembic before the first
  schema change after go-live.
- `audit_events` grows by one row per PC poll (default every 10 minutes);
  plan retention before large fleets.
- A project pushed to "all PCs" reaches only PCs that exist at that moment.
````

- [ ] **Step 4: Run the full suite**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest`
Expected: everything passes (the original 335 plus all `tests/central` tests).

- [ ] **Step 5: Commit**

```bash
git add central tests/central
git commit -m "feat(central): env-based startup, round-trip test and deploy README"
```

---

### Task 10: Threat model and security scan (SDLC A.6 gate)

**Files:**
- Create: `docs/superpowers/specs/2026-10-04-central-api-threat-model.md`
- Modify: `docs/security/` reports (regenerated by the scan script)

**Interfaces:** none (documentation and verification).

- [ ] **Step 1: Write the threat model**

Create `docs/superpowers/specs/2026-10-04-central-api-threat-model.md` with these sections, each filled from the code as built (SDLC_POLICY A.6 requires attack surface, trust boundaries, mitigations):

```markdown
# Central API — Threat Model (SDLC A.6)

## Attack surface
- Admin endpoints (`/admin/*`): session cookie + CSRF; reachable from admin browsers.
- PC endpoints (`/pcs/*`): Bearer API key; reachable from every scanning PC.
- `POST /pcs/register`: unauthenticated by design; protected by a one-time, 60-minute, hashed enrollment code.
- `GET /healthz`: unauthenticated, no data.

## Trust boundaries
Internet/LAN → reverse proxy (TLS) → uvicorn → Postgres. Central never receives scanned documents.

## Threats and mitigations
| Threat | Mitigation | Test |
|---|---|---|
| Admin password guessing | bcrypt, 5-failure lockout (15 min), identical 401 for unknown user | test_admin_auth |
| CSRF on admin mutations | SameSite=strict cookie + X-CSRF-Token compare | test_admin_auth, test_admin_pcs |
| Stolen/rogue PC | per-PC revocable API key; keys and codes stored hashed | test_pcs |
| Enrollment code replay/brute force | single use, expiry, 96-bit code, uniform 400 | test_pcs |
| Cross-PC access | key bound to its own PC id (403) | test_pcs, test_config_pull |
| Secrets leaking into cloud DB | credential-key scan on every config write (422) | test_config_rules, test_admin_config |
| Audit log forging via headers | control chars stripped, 100-char cap | test_security, test_config_pull |
| Resource exhaustion | 256 KB config cap, bounded field lengths | test_admin_config |
| SQL injection | SQLAlchemy ORM only, no raw SQL | (code review) |

## Residual risks / follow-ups
- No rate limit on `/pcs/register` or `/pcs/*` beyond key secrecy: add at the reverse proxy.
- Single admin role; per-admin permissions are out of scope for Phase 1.
- `audit_events` retention is undefined.
```

- [ ] **Step 2: Run the SAST scan and triage**

Run: `.\.venv\Scripts\python.exe scripts/security_scan.py`
Expected: reports regenerated under `docs/security/`. Review any new findings in `central/`; fix High/Medium findings or record a justified disposition in `docs/security/SAST_FINDINGS_REPORT.md` in the same style as the existing entries.

- [ ] **Step 3: Commit**

```bash
git add docs
git commit -m "docs(central): threat model and refreshed SAST reports for the central API"
```

---

## Self-Review

**Spec coverage**
- Data model (pcs, admin_users, projects, project_templates, config_versions, pc_assignments): Task 1 (`current_version` int replaces the spec's `current_version_id`; the `config_versions` table carries the history).
- `POST /pcs/register`: Task 5. `GET /pcs/{id}/config` with `X-Operator-Id`: Task 8. Heartbeat stub: Task 5.
- `PUT /admin/projects/{id}`, `PUT /admin/templates/{id}`, `POST /admin/projects/{id}/push`: Tasks 6–7.
- Admin session auth, one-time-code registration, global poll interval, same repo: Tasks 3–5, 7, 1.
- Security notes (hashed keys, HTTPS, no documents in central DB, SDLC bar): Global Constraints, Tasks 5–6, 10.
- Sync client, local overlay rules, dashboard UI: explicitly plans 2 and 3.

**Placeholder scan:** no TBD/TODO steps; every code step has full code.

**Type consistency:** `save_version(db, kind, row, config, actor) -> bool`, `issue_code(db, pc, settings) -> (code, expires)`, `get_poll_interval(db, default)`, `record(db, actor, action, detail)` and the `enrolled`/`auth` test helpers are used with the same signatures everywhere they appear.

**Review Focus coverage:** credentials rejected (Task 6), cross-PC/revoked key (Task 5, 8), identical resubmit (Task 6), code replay/expiry (Task 5), operator header sanitising (Tasks 2, 8).
