"""SQLite data layer.

Complete schema (HLD §9) created on first run. Later changes go through the
versioned migrations below (SCHEMA_VERSION, _MIGRATIONS), never by editing
_SCHEMA in place for an existing table. WAL journal mode,
foreign keys on, audit_log strictly append-only (no update/delete helpers).

Phase 2: pass `key=` to encrypt with SQLCipher (AES-256). A plaintext
Phase 1 database is migrated once with migrate_to_encrypted().
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
import threading
from typing import Callable
from pathlib import Path

from data.custody_repo import CustodyRepo
from data.documents_repo import DocumentsRepo
from data.projects_repo import ProjectsRepo
from data.users_repo import UsersRepo

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


class Database(ProjectsRepo, DocumentsRepo, CustodyRepo, UsersRepo):
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


    def query(self, sql: str, args=()) -> list[sqlite3.Row]:
        """Run a read-only SELECT (bound parameters only) and return its rows.
        For reporting-style queries that do not belong to a single table's
        accessors; anything but a SELECT is refused."""
        if not sql.lstrip().upper().startswith("SELECT"):
            raise ValueError("query() runs SELECT statements only")
        with self._lock:
            return self.conn.execute(sql, tuple(args)).fetchall()


