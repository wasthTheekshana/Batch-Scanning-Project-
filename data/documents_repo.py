"""Batches, documents, the queue of documents central has not heard about, and jobs.

A mixin of data.db.Database: it relies on the connection (`self.conn`) and the lock (`self._lock`) that class provides.
"""
from __future__ import annotations

import json
import sqlite3
import threading

from data import timeutil


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


class DocumentsRepo:
    conn: sqlite3.Connection         # provided by Database
    _lock: threading.RLock

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

    def insert_batch(self, batch_id: str, project_id: str,
                     operator_user: str) -> None:
        now = timeutil._now()
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

    def insert_document(self, document_id: str, batch_id: str,
                        project_id: str, **fields) -> None:
        now = timeutil._now()
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
        vals.extend([timeutil._now(), document_id])
        with self._lock, self.conn:
            self.conn.execute(
                f"UPDATE documents SET {', '.join(sets)} WHERE document_id=?",
                tuple(vals))
            if _SYNC_COLUMNS.intersection(fields):
                self._touch_outbox(document_id)

    def _wants_outbox(self) -> bool:
        gate = self.outbox_gate
        try:
            return bool(gate and gate())
        except Exception:
            return False

    def _touch_outbox(self, document_id: str) -> None:
        """Runs inside the caller's transaction, so a document and its queue entry are saved together or not at all."""
        if self._wants_outbox():
            self.conn.execute(_TOUCH_OUTBOX, (document_id, timeutil._now()))

    def outbox_enqueue_existing(self) -> int:
        """Queue every document already on this PC (for the first connection to a server). Returns how many."""
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO sync_outbox (document_id, enqueued_at) SELECT document_id, ? FROM documents WHERE true "
                "ON CONFLICT(document_id) DO UPDATE SET "
                "enqueued_at = CASE WHEN seq = sent_seq THEN excluded.enqueued_at ELSE enqueued_at END, "
                "seq = seq + 1, failed = 0, attempts = 0, next_try_at = NULL, last_error = NULL", (timeutil._now(),))
            return int(self.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])

    def outbox_due(self, limit: int, now: str | None = None) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(
                "SELECT document_id, seq, attempts FROM sync_outbox WHERE seq > sent_seq AND failed = 0 "
                "AND (next_try_at IS NULL OR next_try_at <= ?) ORDER BY enqueued_at, document_id LIMIT ?",
                (now or timeutil._now(), limit)).fetchall()

    def outbox_done(self, document_id: str, seq: int) -> None:
        """Reported. If the document changed meanwhile (seq moved on) it stays queued for the newer state."""
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE sync_outbox SET sent_seq = MAX(sent_seq, ?), attempts = 0, next_try_at = NULL, "
                "last_error = NULL, last_sent_at = ? WHERE document_id = ?", (seq, timeutil._now(), document_id))

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

    def upsert_job(self, job_id: str, batch_id: str, project_id: str,
                   page_no: int, status: str, payload_path: str) -> None:
        now = timeutil._now()
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
                (status, timeutil._now(), job_id))

    def get_incomplete_jobs(self) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM jobs WHERE status IN ('queued','in_progress') "
                "ORDER BY page_no").fetchall()
