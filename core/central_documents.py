"""Report finished documents to the central service: the index fields and the PDF's details, never the PDF.

Every document change is queued in the PC database (table sync_outbox) in the same transaction that changes the
document, so a crash or an outage can neither lose nor duplicate a report. A background worker sends what is
queued: the document's details first, then its index fields. The server accepts a repeat harmlessly, so the
only rule needed here is "keep trying until the server says yes". Scanning never waits for any of this.

Design: docs/superpowers/specs/2026-10-15-central-document-sync-design.md (build phase 1).
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from core.central_client import (CentralAuthError, CentralClient, CentralError, CentralRejected,
                                 allow_loopback_http)

log = logging.getLogger(__name__)

BATCH = 25                  # documents sent per pass
INTERVAL = 30.0             # seconds between passes when idle
BACKOFF_START, BACKOFF_MAX = 60, 3600
MAX_FIELDS, MAX_VALUE = 100, 2000
_FIELD_NAME = re.compile(r"[^A-Za-z0-9_.-]")
_hash_cache: dict[tuple[str, int, int], str] = {}


def _clean(value, limit: int = 160) -> str:
    text = "".join(c if c.isprintable() else " " for c in str(value)[:limit * 2])
    return text[:limit]


# ---- what is sent -----------------------------------------------------------------------------------------------
def file_digest(path: Path) -> tuple[int, str] | None:
    """(size, SHA-256) of a file, or None if it is not there. Remembered per (path, modified time, size)."""
    try:
        st = path.stat()
    except OSError:
        return None
    ident = (str(path), st.st_mtime_ns, st.st_size)
    cached = _hash_cache.get(ident)
    if cached is None:
        h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for block in iter(lambda: f.read(1 << 20), b""):
                    h.update(block)
        except OSError:
            return None
        if len(_hash_cache) > 512:
            _hash_cache.clear()
        cached = _hash_cache[ident] = h.hexdigest()
    return st.st_size, cached


def _pages(row) -> int:
    try:
        counted = len(json.loads(row["qc_detail"] or "{}").get("pages", {}))
    except (ValueError, AttributeError):
        counted = 0
    if counted:
        return counted
    m = re.fullmatch(r"(\d+)-(\d+)", row["page_range"] or "")
    return int(m.group(2)) - int(m.group(1)) + 1 if m else 0


def _text(value) -> str | None:
    if value is None or isinstance(value, dict):
        return None
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value)
    return str(value)[:MAX_VALUE]


def build_payload(db, document_id: str) -> tuple[dict, dict] | None:
    """(details, index) for one document as it is now; None when the document no longer exists."""
    row = db.get_document(document_id)
    if row is None:
        return None
    try:
        raw = json.loads(row["extracted_fields"] or "{}")
    except ValueError:
        raw = {}
    fields: dict[str, str] = {}
    for name, value in (raw.items() if isinstance(raw, dict) else ()):
        text = _text(value)
        if text is not None and len(fields) < MAX_FIELDS:
            fields[_FIELD_NAME.sub("_", str(name)).lstrip("-.")[:64] or "field"] = text
    try:
        delivery = json.loads(row["delivery_detail"] or "{}")
    except ValueError:
        delivery = {}
    where = sorted(name for name, d in (delivery.items() if isinstance(delivery, dict) else ())
                   if isinstance(d, dict) and d.get("success"))
    meta: dict = {"project_id": row["project_id"], "batch_id": row["batch_id"], "page_count": _pages(row),
                  "created_at": row["created_at"], "validation_status": row["validation_status"] or "",
                  "delivery_status": row["delivery_status"] or "", "delivered_to": where}
    path = Path(row["storage_path"]) if row["storage_path"] else None
    digest = file_digest(path) if path is not None else None
    if path is not None and digest is not None:
        meta.update(file_name=path.name[:255], size=digest[0], sha256=digest[1],
                    delivered_to=[*where, "kept on the scanning PC"])
    central = db.get_central_project(row["project_id"])
    index = {"fields": fields, "validation_status": row["validation_status"] or "",
             "project_version": int(central["central_version"]) if central is not None else None,
             "edited_at": row["updated_at"]}
    return meta, index


# ---- sending ---------------------------------------------------------------------------------------------------------
@dataclass
class PushResult:
    sent: int = 0
    retry_later: int = 0
    refused: int = 0
    deleted: int = 0                    # documents the server says an administrator deleted: dropped from the queue
    stopped: str | None = None          # why the pass ended early: not_connected | access_refused | unavailable


def _stamp(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


class DocumentPusher:
    def __init__(self, db, state, client_factory: Callable[[str, float], CentralClient] | None = None,
                 timeout: float = 15.0, now: Callable[[], datetime] | None = None):
        self.db, self.state = db, state
        self._factory = client_factory or (lambda url, t: CentralClient(
            url, timeout=t, allow_insecure_loopback=allow_loopback_http()))
        self.timeout = timeout
        self._now = now or (lambda: datetime.now(timezone.utc))

    def push_once(self) -> PushResult:
        result = PushResult()
        s = self.state.settings
        if not self.state.is_registered():
            result.stopped = "not_connected"
            return result
        if s.auth_failed:
            result.stopped = "access_refused"
            return result
        api_key = self.state.api_key()
        if not api_key:
            result.stopped = "not_connected"
            return result
        try:
            client = self._factory(s.server_url, self.timeout)
        except ValueError:
            result.stopped = "not_connected"
            return result
        now = self._now()
        for item in self.db.outbox_due(BATCH, _stamp(now)):
            doc_id, seq = item["document_id"], int(item["seq"])
            built = build_payload(self.db, doc_id)
            if built is None:
                self.db.outbox_drop(doc_id)
                continue
            try:
                client.put_document(s.pc_id, api_key, doc_id, "meta", built[0])
                client.put_document(s.pc_id, api_key, doc_id, "index", built[1])
            except CentralAuthError:
                result.stopped = "access_refused"           # the regular sync notices and asks for a new code
                return result
            except CentralRejected as e:
                if e.status == 410:                          # an administrator deleted it on the server: do not send it again
                    self.db.outbox_drop(doc_id)
                    result.deleted += 1
                    continue
                self.db.outbox_fail(doc_id, _clean(e))
                result.refused += 1
                continue
            except (CentralError, OSError, ValueError) as e:
                self._later(doc_id, int(item["attempts"]), _clean(e), now)
                result.retry_later += 1
                result.stopped = "unavailable"              # the server is probably down: stop hammering it
                return result
            except Exception as e:                           # never let one document stop the worker for good
                self._later(doc_id, int(item["attempts"]), type(e).__name__, now)
                result.retry_later += 1
                continue
            self.db.outbox_done(doc_id, seq)
            result.sent += 1
        return result

    def _later(self, doc_id: str, attempts: int, error: str, now: datetime) -> None:
        delay = min(BACKOFF_MAX, BACKOFF_START * 2 ** min(attempts, 6)) * random.uniform(0.8, 1.2)
        self.db.outbox_retry(doc_id, error, _stamp(now + timedelta(seconds=delay)))


class DocumentSyncWorker:
    """Runs push_once on a daemon thread. Never raises into the app."""

    def __init__(self, pusher: DocumentPusher, interval: float = INTERVAL, join_timeout: float = 5.0):
        self.pusher, self.interval, self._join_timeout = pusher, interval, join_timeout
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="central-documents", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=self._join_timeout)

    def push_now(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            wait = self.interval
            try:
                result = self.pusher.push_once()
                if result.sent and not result.stopped:
                    wait = 0.5                               # more may be queued: carry on straight away
            except Exception:
                log.exception("sending documents to central failed")
            self._wake.wait(wait)
            self._wake.clear()
