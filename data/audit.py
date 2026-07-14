"""Audit logging (HLD §10.3).

Every action writes an append-only row to audit_log AND a line to a
rotating file log (secondary copy). When a document_id is given the
document's own audit_trail JSON array also grows.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5


class AuditLogger:
    def __init__(self, db, log_dir: str | Path):
        self.db = db
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        self._file_logger = logging.getLogger(f"dok.audit.{id(self)}")
        self._file_logger.setLevel(logging.INFO)
        self._file_logger.propagate = False
        handler = logging.handlers.RotatingFileHandler(
            log_dir / "audit.log", maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        self._file_logger.addHandler(handler)

    def log(self, action: str, user: str | None,
            document_id: str | None = None, **detail) -> None:
        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        self.db.append_audit(document_id, user, action, detail)
        self._file_logger.info(json.dumps({
            "timestamp": ts, "action": action, "user": user,
            "document_id": document_id, "detail": detail}))
        if document_id:
            doc = self.db.get_document(document_id)
            if doc is not None:
                trail = json.loads(doc["audit_trail"] or "[]")
                trail.append({"timestamp": ts, "action": action,
                              "user": user, "detail": detail})
                self.db.update_document(document_id, audit_trail=trail)

    def close(self) -> None:
        for h in list(self._file_logger.handlers):
            h.close()
            self._file_logger.removeHandler(h)
