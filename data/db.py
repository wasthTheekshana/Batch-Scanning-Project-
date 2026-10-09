"""SQLite data layer.

Complete schema (HLD §9) created on first run — all Phase 1/2/3 columns
present from day one so no migrations are ever needed. WAL journal mode,
foreign keys on, audit_log strictly append-only (no update/delete helpers).

Phase 2: pass `key=` to encrypt with SQLCipher (AES-256). A plaintext
Phase 1 database is migrated once with migrate_to_encrypted().
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

try:
    from sqlcipher3 import dbapi2 as _sqlcipher
except ImportError:
    _sqlcipher = None


def _key_pragma_value(key: bytes) -> str:
    return f"\"x'{key.hex()}'\""


def is_encrypted(path: str | Path) -> bool:
    """A plaintext SQLite file starts with the magic header; an
    SQLCipher file starts with its random salt."""
    p = Path(path)
    if not p.exists() or p.stat().st_size < 16:
        return False
    return p.read_bytes()[:16] != b"SQLite format 3\x00"


def migrate_to_encrypted(plain_path: str | Path, enc_path: str | Path,
                         key: bytes) -> None:
    """One-time Phase 1 → Phase 2 migration via sqlcipher_export."""
    if _sqlcipher is None:
        raise RuntimeError("sqlcipher3 not installed — cannot encrypt")
    conn = _sqlcipher.connect(str(plain_path))
    try:
        conn.execute(
            f"ATTACH DATABASE '{Path(enc_path).as_posix()}' AS encrypted "
            f"KEY {_key_pragma_value(key)}")
        conn.execute("SELECT sqlcipher_export('encrypted')")
        conn.execute("DETACH DATABASE encrypted")
    finally:
        conn.close()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    project_id   TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1,
    status       TEXT NOT NULL DEFAULT 'active',
    config_json  TEXT NOT NULL,
    cloned_from  TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS batches (
    batch_id      TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(project_id),
    operator_user TEXT,
    scan_date     TEXT NOT NULL,
    page_count    INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'in_progress',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    document_id         TEXT PRIMARY KEY,
    batch_id            TEXT NOT NULL REFERENCES batches(batch_id),
    project_id          TEXT NOT NULL,
    page_range          TEXT,
    enhancement_profile TEXT,
    extracted_fields    TEXT NOT NULL DEFAULT '{}',
    validation_status   TEXT NOT NULL DEFAULT 'pending_qc',
    qc_score            INTEGER,
    qc_detail           TEXT NOT NULL DEFAULT '{}',
    storage_path        TEXT,
    delivery_status     TEXT NOT NULL DEFAULT 'pending',
    delivery_detail     TEXT NOT NULL DEFAULT '{}',
    duplicate_of        TEXT,
    audit_trail         TEXT NOT NULL DEFAULT '[]',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bundles (
    bundle_id    TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL REFERENCES projects(project_id),
    label        TEXT NOT NULL,
    qr_value     TEXT NOT NULL UNIQUE,
    status       TEXT NOT NULL DEFAULT 'received',
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS custody_events (
    event_id     TEXT PRIMARY KEY,
    bundle_id    TEXT NOT NULL REFERENCES bundles(bundle_id),
    batch_id     TEXT,
    stage        TEXT NOT NULL,
    operator     TEXT,
    timestamp    TEXT NOT NULL,
    detail       TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS project_templates (
    template_id  TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    category     TEXT NOT NULL,
    config_json  TEXT NOT NULL,
    built_in     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    user_id       TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL,
    last_login    TEXT,
    active        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS audit_log (
    log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT,
    user        TEXT,
    action      TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '{}',
    timestamp   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id       TEXT PRIMARY KEY,
    batch_id     TEXT NOT NULL,
    project_id   TEXT NOT NULL,
    page_no      INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'queued',
    payload_path TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS central_projects (
    project_id      TEXT PRIMARY KEY,
    central_version INTEGER NOT NULL,
    managed         INTEGER NOT NULL DEFAULT 1,
    applied_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_batch ON documents(batch_id);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(validation_status);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_audit_doc ON audit_log(document_id);
CREATE INDEX IF NOT EXISTS idx_custody_bundle ON custody_events(bundle_id);
"""

_DOC_JSON_FIELDS = {"extracted_fields", "qc_detail", "delivery_detail", "audit_trail"}
_DOC_COLUMNS = {
    "page_range", "enhancement_profile", "extracted_fields",
    "validation_status", "qc_score", "qc_detail", "storage_path",
    "delivery_status", "delivery_detail", "duplicate_of", "audit_trail",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Database:
    """Thread-safe SQLite wrapper. One connection guarded by a lock —
    fine at pipeline throughput; every write is a single short transaction."""

    def __init__(self, path: str | Path, key: bytes | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.encrypted = False
        if key is not None and _sqlcipher is not None:
            self.conn = _sqlcipher.connect(str(self.path),
                                           check_same_thread=False)
            self.conn.execute(f"PRAGMA key = {_key_pragma_value(key)}")
            self.conn.row_factory = _sqlcipher.Row
            self.encrypted = True
        else:
            if key is not None:
                log.warning("sqlcipher3 not installed — falling back to "
                            "UNENCRYPTED database")
            self.conn = sqlite3.connect(str(self.path),
                                        check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.init_schema()

    def init_schema(self) -> None:
        with self._lock, self.conn:
            self.conn.executescript(_SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # -- projects ---------------------------------------------------------
    def insert_project(self, project_id: str, name: str, config: dict,
                       version: int = 1, status: str = "active",
                       cloned_from: str | None = None) -> None:
        now = _now()
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO projects VALUES (?,?,?,?,?,?,?,?)",
                (project_id, name, version, status, json.dumps(config),
                 cloned_from, now, now))

    def update_project(self, project_id: str, name: str, config: dict,
                       version: int, status: str = "active") -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE projects SET name=?, config_json=?, version=?, "
                "status=?, updated_at=? WHERE project_id=?",
                (name, json.dumps(config), version, status, _now(), project_id))

    def get_project(self, project_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM projects WHERE project_id=?",
                (project_id,)).fetchone()

    def list_projects(self, status: str | None = None) -> list[sqlite3.Row]:
        q = "SELECT * FROM projects"
        args: tuple = ()
        if status:
            q += " WHERE status=?"
            args = (status,)
        with self._lock:
            return self.conn.execute(q + " ORDER BY name", args).fetchall()

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

    # -- batches ----------------------------------------------------------
    def insert_batch(self, batch_id: str, project_id: str,
                     operator_user: str) -> None:
        now = _now()
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO batches VALUES (?,?,?,?,?,?,?)",
                (batch_id, project_id, operator_user, now, 0,
                 "in_progress", now))

    def update_batch_status(self, batch_id: str, status: str,
                            page_count: int | None = None) -> None:
        with self._lock, self.conn:
            if page_count is None:
                self.conn.execute(
                    "UPDATE batches SET status=? WHERE batch_id=?",
                    (status, batch_id))
            else:
                self.conn.execute(
                    "UPDATE batches SET status=?, page_count=? WHERE batch_id=?",
                    (status, page_count, batch_id))

    # -- documents --------------------------------------------------------
    def insert_document(self, document_id: str, batch_id: str,
                        project_id: str, **fields) -> None:
        now = _now()
        base = {
            "document_id": document_id, "batch_id": batch_id,
            "project_id": project_id, "created_at": now, "updated_at": now,
        }
        for k, v in fields.items():
            if k not in _DOC_COLUMNS:
                raise ValueError(f"unknown document column: {k}")
            base[k] = json.dumps(v) if k in _DOC_JSON_FIELDS and not isinstance(v, str) else v
        cols = ",".join(base)
        marks = ",".join("?" * len(base))
        with self._lock, self.conn:
            self.conn.execute(
                f"INSERT INTO documents ({cols}) VALUES ({marks})",
                tuple(base.values()))

    def update_document(self, document_id: str, **fields) -> None:
        sets, vals = [], []
        for k, v in fields.items():
            if k not in _DOC_COLUMNS:
                raise ValueError(f"unknown document column: {k}")
            sets.append(f"{k}=?")
            vals.append(json.dumps(v) if k in _DOC_JSON_FIELDS and not isinstance(v, str) else v)
        sets.append("updated_at=?")
        vals.extend([_now(), document_id])
        with self._lock, self.conn:
            self.conn.execute(
                f"UPDATE documents SET {', '.join(sets)} WHERE document_id=?",
                tuple(vals))

    def get_document(self, document_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM documents WHERE document_id=?",
                (document_id,)).fetchone()

    def list_documents(self, batch_id: str | None = None,
                       validation_status: str | None = None) -> list[sqlite3.Row]:
        clauses, args = [], []
        if batch_id:
            clauses.append("batch_id=?")
            args.append(batch_id)
        if validation_status:
            clauses.append("validation_status=?")
            args.append(validation_status)
        q = "SELECT * FROM documents"
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        with self._lock:
            return self.conn.execute(q + " ORDER BY created_at", tuple(args)).fetchall()

    def known_field_values(self, field_name: str) -> set[str]:
        """Distinct previously-indexed values for an extracted field, used
        for autocomplete and lookup-table validation (HLD §4 Layer 5)."""
        values: set[str] = set()
        with self._lock:
            rows = self.conn.execute(
                "SELECT extracted_fields FROM documents").fetchall()
        for row in rows:
            try:
                v = json.loads(row["extracted_fields"] or "{}").get(field_name)
            except json.JSONDecodeError:
                continue
            if v:
                values.add(str(v))
        return values

    # -- custody (HLD enhancement §3.2) ------------------------------------
    def insert_bundle(self, bundle_id: str, project_id: str, label: str,
                      qr_value: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO bundles (bundle_id, project_id, label, "
                "qr_value, status, created_at) VALUES (?,?,?,?,?,?)",
                (bundle_id, project_id, label, qr_value, "received", _now()))

    def get_bundle(self, bundle_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM bundles WHERE bundle_id=?",
                (bundle_id,)).fetchone()

    def get_bundle_by_qr(self, qr_value: str) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM bundles WHERE qr_value=?",
                (qr_value,)).fetchone()

    def list_bundles(self, project_id: str | None = None) -> list[sqlite3.Row]:
        q = "SELECT * FROM bundles"
        args: tuple = ()
        if project_id:
            q += " WHERE project_id=?"
            args = (project_id,)
        with self._lock:
            return self.conn.execute(q + " ORDER BY created_at", args).fetchall()

    def update_bundle_status(self, bundle_id: str, status: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE bundles SET status=? WHERE bundle_id=?",
                (status, bundle_id))

    def insert_custody_event(self, event_id: str, bundle_id: str, stage: str,
                             operator: str | None, batch_id: str | None = None,
                             detail: dict | None = None) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO custody_events (event_id, bundle_id, batch_id, "
                "stage, operator, timestamp, detail) VALUES (?,?,?,?,?,?,?)",
                (event_id, bundle_id, batch_id, stage, operator, _now(),
                 json.dumps(detail or {})))

    def list_custody_events(self, bundle_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM custody_events WHERE bundle_id=? "
                "ORDER BY timestamp", (bundle_id,)).fetchall()

    # -- project templates (Enhancement Plan §4.4) -------------------------
    def insert_template(self, template_id: str, name: str, category: str,
                        config: dict, built_in: bool = False) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO project_templates (template_id, name, "
                "category, config_json, built_in, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (template_id, name, category, json.dumps(config),
                 int(built_in), _now()))

    def get_template(self, template_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM project_templates WHERE template_id=?",
                (template_id,)).fetchone()

    def list_templates(self) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM project_templates "
                "ORDER BY category, name").fetchall()

    def update_template_config(self, template_id: str, config: dict) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE project_templates SET config_json=? "
                "WHERE template_id=?", (json.dumps(config), template_id))

    # -- users ------------------------------------------------------------
    def insert_user(self, user_id: str, username: str, password_hash: str,
                    role: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO users (user_id, username, password_hash, role) "
                "VALUES (?,?,?,?)", (user_id, username, password_hash, role))

    def get_user(self, username: str) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM users WHERE username=?", (username,)).fetchone()

    def set_password_hash(self, username: str, password_hash: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE users SET password_hash=? WHERE username=?",
                (password_hash, username))

    def set_user_active(self, username: str, active: bool) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE users SET active=? WHERE username=?",
                (1 if active else 0, username))

    def set_user_role(self, username: str, role: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE users SET role=? WHERE username=?", (role, username))

    def list_users(self) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM users ORDER BY username").fetchall()

    def touch_login(self, username: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE users SET last_login=? WHERE username=?",
                (_now(), username))

    # -- audit (append-only) ----------------------------------------------
    def append_audit(self, document_id: str | None, user: str | None,
                     action: str, detail: dict | None = None) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO audit_log (document_id, user, action, detail, timestamp) "
                "VALUES (?,?,?,?,?)",
                (document_id, user, action, json.dumps(detail or {}), _now()))

    # -- jobs (crash recovery) ---------------------------------------------
    def upsert_job(self, job_id: str, batch_id: str, project_id: str,
                   page_no: int, status: str, payload_path: str) -> None:
        now = _now()
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(job_id) DO UPDATE SET status=excluded.status, "
                "payload_path=excluded.payload_path, updated_at=excluded.updated_at",
                (job_id, batch_id, project_id, page_no, status,
                 payload_path, now, now))

    def mark_job(self, job_id: str, status: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE jobs SET status=?, updated_at=? WHERE job_id=?",
                (status, _now(), job_id))

    def get_incomplete_jobs(self) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM jobs WHERE status IN ('queued','in_progress') "
                "ORDER BY page_no").fetchall()
