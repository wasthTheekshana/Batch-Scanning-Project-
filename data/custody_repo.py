"""Chain-of-custody bundles, their events and the documents scanned under them.

A mixin of data.db.Database: it relies on the connection (`self.conn`) and the lock (`self._lock`) that class provides.
"""
from __future__ import annotations

import json
import sqlite3
import threading

from data import timeutil


class CustodyRepo:
    conn: sqlite3.Connection         # provided by Database
    _lock: threading.RLock

    def insert_bundle(self, bundle_id: str, project_id: str, label: str,
                      qr_value: str, expected_documents: int | None = None) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO bundles (bundle_id, project_id, label, "
                "qr_value, status, created_at, expected_documents) "
                "VALUES (?,?,?,?,?,?,?)",
                (bundle_id, project_id, label, qr_value, "received", timeutil._now(),
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
                (event_id, bundle_id, batch_id, stage, operator, timeutil._now(),
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
                (bundle_id, document_id, batch_id, timeutil._now()))

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
