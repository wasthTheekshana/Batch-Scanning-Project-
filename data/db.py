"""SQLite data layer.

Complete schema (HLD §9) created on first run. Later changes go through the
versioned migrations below (SCHEMA_VERSION, _MIGRATIONS), never by editing
_SCHEMA in place for an existing table. WAL journal mode,
foreign keys on, audit_log strictly append-only (no update/delete helpers).

Phase 2: pass `key=` to encrypt with SQLCipher (AES-256). A plaintext
Phase 1 database is migrated once with migrate_to_encrypted().
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Callable
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

# -- schema versioning ----------------------------------------------------
# Version 1 is everything in _SCHEMA above (databases made before versioning
# existed are adopted as version 1: _SCHEMA only creates what is missing).
# To change the schema: append (next_number, "SQL ...") to _MIGRATIONS and set
# SCHEMA_VERSION to that number. Each migration runs in one transaction with its
# version stamp; a failure rolls everything back, and a copy of the database
# is kept first as <name>.before-v<N>.bak.
SCHEMA_VERSION = 5
_MIGRATIONS: list[tuple[int, str]] = [
    # 2: which documents were scanned under which custody bundle's QR label
    (2, """
CREATE TABLE IF NOT EXISTS bundle_documents (
    bundle_id    TEXT NOT NULL REFERENCES bundles(bundle_id),
    document_id  TEXT NOT NULL REFERENCES documents(document_id),
    batch_id     TEXT NOT NULL,
    linked_at    TEXT NOT NULL,
    PRIMARY KEY (bundle_id, document_id)
);
CREATE INDEX IF NOT EXISTS idx_bundle_documents_bundle ON bundle_documents(bundle_id);
"""),
    # 3: how many documents a bundle is expected to hold (optional)
    (3, "ALTER TABLE bundles ADD COLUMN expected_documents INTEGER;"),
    # 4: sign-in protection: consecutive wrong passwords and the lock that follows
    (4, """
ALTER TABLE users ADD COLUMN failed_logins INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN locked_until TEXT;
"""),
    # 5: documents waiting to be reported to the central service (one row per document; seq counts its changes)
    (5, """
CREATE TABLE IF NOT EXISTS sync_outbox (
    document_id  TEXT PRIMARY KEY,
    seq          INTEGER NOT NULL DEFAULT 1,
    sent_seq     INTEGER NOT NULL DEFAULT 0,
    attempts     INTEGER NOT NULL DEFAULT 0,
    next_try_at  TEXT,
    last_error   TEXT,
    failed       INTEGER NOT NULL DEFAULT 0,
    enqueued_at  TEXT NOT NULL,
    last_sent_at TEXT
);
"""),
]


class MigrationError(Exception):
    """A schema migration failed; the database was left as it was."""


class DatabaseTooNew(Exception):
    """The database was written by a newer release than this one."""


_DOC_JSON_FIELDS = {"extracted_fields", "qc_detail", "delivery_detail", "audit_trail"}
# a change to one of these is something central should hear about (audit_trail and qc_detail change too often to matter)
_SYNC_COLUMNS = {"page_range", "extracted_fields", "validation_status", "storage_path", "delivery_status",
                 "delivery_detail"}
_TOUCH_OUTBOX = (
    "INSERT INTO sync_outbox (document_id, enqueued_at) VALUES (?, ?) "
    "ON CONFLICT(document_id) DO UPDATE SET "
    "enqueued_at = CASE WHEN seq = sent_seq THEN excluded.enqueued_at ELSE enqueued_at END, "
    "seq = seq + 1, failed = 0, attempts = 0, next_try_at = NULL, last_error = NULL")
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
        # Set by the central wiring: true while this PC is enrolled, so documents are queued for reporting only then.
        self.outbox_gate: Callable[[], bool] | None = None
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.init_schema()

    def init_schema(self) -> None:
        with self._lock:
            # a brand-new file has nothing to protect: it is brought to the
            # current version without a backup copy
            fresh = self.conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 0
        with self._lock, self.conn:
            self.conn.executescript(_SCHEMA)
        self._migrate(backup=not fresh)

    def _user_version(self) -> int:
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def _migrate(self, backup: bool = True) -> None:
        with self._lock:
            current = self._user_version()
            if current > SCHEMA_VERSION:
                raise DatabaseTooNew(
                    f"{self.path.name} is version {current}; this release "
                    f"only understands up to {SCHEMA_VERSION}. Install the "
                    "newer release.")
            pending = sorted((v, sql) for v, sql in _MIGRATIONS if v > current)
            if current < 1 and not pending:
                self.conn.execute("PRAGMA user_version = 1")
                return
            if not pending:
                return
            if backup:
                self._backup_before(pending[0][0])
            for number, sql in pending:
                try:
                    self.conn.executescript(
                        "BEGIN;\n" + sql + f"\nPRAGMA user_version = {number};"
                        "\nCOMMIT;")
                except Exception as e:
                    self.conn.rollback()
                    if "duplicate column name" in str(e):
                        # the column is already there (a copy that lost its version
                        # stamp): the change is in place, only the stamp is missing
                        self.conn.execute(f"PRAGMA user_version = {number}")
                        continue
                    raise MigrationError(
                        f"schema migration to version {number} failed "
                        f"({e}); the database was left at version "
                        f"{self._user_version()}") from e
                log.info("database migrated to version %d", number)

    def _backup_before(self, number: int) -> None:
        """Copy the database aside before its first pending migration."""
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            shutil.copy2(self.path, self.path.with_name(
                f"{self.path.name}.before-v{number}.bak"))
        except Exception as e:
            raise MigrationError(
                f"could not back up {self.path.name} before migrating: {e}"
            ) from e

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

    def activity_for_day(self, day: str) -> dict[str, int]:
        """How much this PC processed on one UTC day (YYYY-MM-DD): counts only. `errors` are the
        day's documents that were rejected or whose delivery was reported unsuccessful."""
        like = day + "%"
        with self._lock:
            documents, errors = self.conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(validation_status = 'rejected' "
                "OR delivery_detail LIKE '%\"success\": false%'), 0) "
                "FROM documents WHERE created_at LIKE ?", (like,)).fetchone()
            batches, pages = self.conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(page_count), 0) FROM batches "
                "WHERE created_at LIKE ?", (like,)).fetchone()
        return {"documents": int(documents), "pages": int(pages), "batches": int(batches),
                "errors": int(errors)}

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

    def get_batch(self, batch_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()

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
            self._touch_outbox(document_id)

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
            if _SYNC_COLUMNS.intersection(fields):
                self._touch_outbox(document_id)

    # -- the outbox: documents central has not yet been told about ------------------------------------
    def _wants_outbox(self) -> bool:
        gate = self.outbox_gate
        try:
            return bool(gate and gate())
        except Exception:
            return False

    def _touch_outbox(self, document_id: str) -> None:
        """Runs inside the caller's transaction, so a document and its queue entry are saved together or not at all."""
        if self._wants_outbox():
            self.conn.execute(_TOUCH_OUTBOX, (document_id, _now()))

    def outbox_enqueue_existing(self) -> int:
        """Queue every document already on this PC (for the first connection to a server). Returns how many."""
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO sync_outbox (document_id, enqueued_at) SELECT document_id, ? FROM documents WHERE true "
                "ON CONFLICT(document_id) DO UPDATE SET "
                "enqueued_at = CASE WHEN seq = sent_seq THEN excluded.enqueued_at ELSE enqueued_at END, "
                "seq = seq + 1, failed = 0, attempts = 0, next_try_at = NULL, last_error = NULL", (_now(),))
            return int(self.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])

    def outbox_due(self, limit: int, now: str | None = None) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(
                "SELECT document_id, seq, attempts FROM sync_outbox WHERE seq > sent_seq AND failed = 0 "
                "AND (next_try_at IS NULL OR next_try_at <= ?) ORDER BY enqueued_at, document_id LIMIT ?",
                (now or _now(), limit)).fetchall()

    def outbox_done(self, document_id: str, seq: int) -> None:
        """Reported. If the document changed meanwhile (seq moved on) it stays queued for the newer state."""
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE sync_outbox SET sent_seq = MAX(sent_seq, ?), attempts = 0, next_try_at = NULL, "
                "last_error = NULL, last_sent_at = ? WHERE document_id = ?", (seq, _now(), document_id))

    def outbox_retry(self, document_id: str, error: str, next_try_at: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE sync_outbox SET attempts = attempts + 1, next_try_at = ?, last_error = ? WHERE document_id = ?",
                (next_try_at, error, document_id))

    def outbox_fail(self, document_id: str, error: str) -> None:
        """The server refused this document for good; it waits for the next change or an explicit retry."""
        with self._lock, self.conn:
            self.conn.execute("UPDATE sync_outbox SET failed = 1, last_error = ? WHERE document_id = ?",
                              (error, document_id))

    def outbox_drop(self, document_id: str) -> None:
        with self._lock, self.conn:
            self.conn.execute("DELETE FROM sync_outbox WHERE document_id = ?", (document_id,))

    def outbox_retry_failed(self) -> int:
        with self._lock, self.conn:
            return self.conn.execute(
                "UPDATE sync_outbox SET failed = 0, attempts = 0, next_try_at = NULL WHERE failed = 1").rowcount

    def outbox_stats(self) -> dict:
        """waiting (queued, will be tried), failed (refused for good), the oldest waiting item's time, the last
        successful report and the last error. Counts and times only."""
        with self._lock:
            waiting, oldest = self.conn.execute(
                "SELECT COUNT(*), MIN(enqueued_at) FROM sync_outbox WHERE seq > sent_seq AND failed = 0").fetchone()
            failed = self.conn.execute("SELECT COUNT(*) FROM sync_outbox WHERE failed = 1").fetchone()[0]
            last_sent = self.conn.execute("SELECT MAX(last_sent_at) FROM sync_outbox").fetchone()[0]
            error = self.conn.execute(
                "SELECT last_error FROM sync_outbox WHERE last_error IS NOT NULL "
                "ORDER BY COALESCE(next_try_at, '') DESC LIMIT 1").fetchone()
        return {"waiting": int(waiting), "failed": int(failed), "oldest": oldest, "last_sent_at": last_sent,
                "last_error": error[0] if error else None}

    def get_document(self, document_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM documents WHERE document_id=?",
                (document_id,)).fetchone()

    def query(self, sql: str, args=()) -> list[sqlite3.Row]:
        """Run a read-only SELECT (bound parameters only) and return its rows.
        For reporting-style queries that do not belong to a single table's
        accessors; anything but a SELECT is refused."""
        if not sql.lstrip().upper().startswith("SELECT"):
            raise ValueError("query() runs SELECT statements only")
        with self._lock:
            return self.conn.execute(sql, tuple(args)).fetchall()

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

    def known_field_values(self, field_name: str,
                           project_id: str | None = None) -> set[str]:
        """Distinct previously-indexed values for an extracted field, used
        for autocomplete and lookup-table validation (HLD §4 Layer 5).
        Restricted to one project's documents when project_id is given."""
        values: set[str] = set()
        with self._lock:
            if project_id is None:
                rows = self.conn.execute(
                    "SELECT extracted_fields FROM documents").fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT extracted_fields FROM documents WHERE project_id=?",
                    (project_id,)).fetchall()
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
                      qr_value: str, expected_documents: int | None = None) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO bundles (bundle_id, project_id, label, "
                "qr_value, status, created_at, expected_documents) "
                "VALUES (?,?,?,?,?,?,?)",
                (bundle_id, project_id, label, qr_value, "received", _now(),
                 expected_documents))

    def set_bundle_expected(self, bundle_id: str, expected: int | None) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE bundles SET expected_documents=? WHERE bundle_id=?",
                (expected, bundle_id))

    def documents_without_bundle(self) -> list[sqlite3.Row]:
        """Documents not yet linked to any custody bundle."""
        with self._lock:
            return self.conn.execute(
                "SELECT document_id, batch_id, qc_detail FROM documents WHERE "
                "document_id NOT IN (SELECT document_id FROM bundle_documents) "
                "ORDER BY created_at").fetchall()

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

    def link_bundle_document(self, bundle_id: str, document_id: str,
                             batch_id: str) -> None:
        """Record that a document was scanned under this bundle's QR label."""
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO bundle_documents (bundle_id, document_id, "
                "batch_id, linked_at) VALUES (?,?,?,?)",
                (bundle_id, document_id, batch_id, _now()))

    def list_bundle_documents(self, bundle_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(
                "SELECT d.document_id, d.batch_id, d.page_range, d.validation_status, "
                "d.delivery_status, d.extracted_fields, d.created_at "
                "FROM bundle_documents bd JOIN documents d "
                "ON d.document_id = bd.document_id WHERE bd.bundle_id=? "
                "ORDER BY d.created_at, d.rowid", (bundle_id,)).fetchall()

    def bundles_of_document(self, document_id: str) -> list[str]:
        with self._lock:
            return [r[0] for r in self.conn.execute(
                "SELECT bundle_id FROM bundle_documents WHERE document_id=?",
                (document_id,))]

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

    def record_login_failure(self, username: str, max_failures: int,
                             lock_seconds: int) -> tuple[int, str | None]:
        """Count one more wrong password; on reaching `max_failures` lock the account for
        `lock_seconds`. Returns (failures so far, locked_until or None)."""
        from datetime import timedelta
        with self._lock, self.conn:
            row = self.conn.execute(
                "SELECT failed_logins FROM users WHERE username=?", (username,)).fetchone()
            if row is None:
                return 0, None
            failures = int(row["failed_logins"]) + 1
            locked_until = None
            if failures >= max_failures:
                locked_until = (datetime.now(timezone.utc)
                                + timedelta(seconds=lock_seconds)).isoformat()
                failures = 0                    # the lock is the penalty; count afresh after it
            self.conn.execute("UPDATE users SET failed_logins=?, locked_until=? WHERE username=?",
                              (failures, locked_until, username))
            return failures, locked_until

    def clear_login_failures(self, username: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE users SET failed_logins=0, locked_until=NULL WHERE username=?", (username,))

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
