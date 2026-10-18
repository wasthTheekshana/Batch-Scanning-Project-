"""Apply the projects central assigned to this PC (spec: sync client).

CentralSyncService.sync_once() is safe to call from any thread and never
raises: every outcome is a SyncResult. It never touches the running pipeline
- new or changed projects are written to the local project store/database and
take effect for the pipeline at the start of the next scan batch (no restart).

Concurrency: no network call (pull or enrollment) is made under the service
lock. Under it run only local steps: a sync's apply phase and its state
writes, and the local part of register() (storing the result) and
disconnect(). So a register/disconnect cannot interleave with a half-applied
sync. A sync also snapshots CentralState.generation before its pull and
discards its outcome if the registration changed meanwhile.

Everything that comes from the server (project ids, exception text) is
untrusted: it is stripped of control characters and truncated before it
reaches an audit entry, a log line or a SyncResult.
"""
from __future__ import annotations

import copy
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from core.central_client import (CentralAuthError, CentralClient,
                                 CentralError, allow_loopback_http)
from core.central_state import CentralState
from core.config_overlay import (LOCAL_ONLY_KEYS, PulledProjectError,
                                 default_local_sections, overlay,
                                 validate_entry)
from core.profile_manager import ProfileManager, ProjectConfigError
from core.project_store import config_error_path, load_config

log = logging.getLogger(__name__)

ACTOR = "central-sync"
MAX_PROJECTS = 500          # entries processed per pull; the rest are skipped
MAX_TEXT = 200
_CHANGED = "registration changed during sync"
UNREADABLE_LOCAL_REASON = ("local copy unreadable - repair or re-save the "
                           "project, then sync again")
UNREADABLE_FILE_REASON = ("local project file unreadable - repair or re-save "
                          "the project, then sync again")


class LocalCopyUnreadable(Exception):
    """The PC's stored copy of a project cannot be read; the entry is
    skipped (fixed text, never content)."""


@dataclass
class SyncResult:
    status: str  # not_registered | ok | auth_failed | unavailable
    applied: list[str] = field(default_factory=list)
    unchanged: int = 0
    unassigned: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    message: str = ""


def default_client_factory(server_url: str, timeout: float) -> CentralClient:
    return CentralClient(server_url, timeout=timeout,
                         allow_insecure_loopback=allow_loopback_http())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z")


def _clean(value, limit: int = MAX_TEXT) -> str:
    """Untrusted text made safe for logs/audit: no control chars, bounded."""
    text = str(value)[:limit * 2]
    text = "".join(c if c.isprintable() else " " for c in text)
    return text[:limit]


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
        # Serialises the local steps of register/disconnect with the apply
        # phase of a sync. No network call is ever made while holding it.
        self._lock = threading.RLock()

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
        CentralUnavailable; leaves the local state untouched on failure.
        The network call is made without the lock; only storing the result
        waits for a sync that is applying projects (local I/O only)."""
        url = server_url.strip().rstrip("/")
        client = self._client_factory(url, self.timeout)
        result = client.register(code.strip())
        with self._lock:
            self.state.register_result(url, result.pc_id, result.api_key,
                                       result.poll_interval_seconds)
            self._audit("central_registered", pc_id=_clean(result.pc_id),
                        server=_clean(url))

    def disconnect(self) -> None:
        """Forget the registration; keep every local project and its data.
        Waits for a sync that is applying projects (local I/O only)."""
        with self._lock:
            self.db.unmanage_all_central_projects()
            self.state.clear()
            self._audit("central_disconnected")

    # -- the sync itself ---------------------------------------------------------
    def sync_once(self) -> SyncResult:
        """Never raises: any unexpected failure becomes "unavailable"."""
        try:
            return self._sync_once()
        except Exception as e:
            log.warning("central sync failed unexpectedly (%s)",
                        type(e).__name__)
            return SyncResult("unavailable",
                              message=_clean(f"{type(e).__name__}: {e}"))

    def _sync_once(self) -> SyncResult:
        generation = self.state.generation   # read BEFORE the settings
        if not self.state.is_registered():
            return SyncResult("not_registered")
        s = self.state.settings
        if s.auth_failed:
            return SyncResult("auth_failed",
                              message="re-enrollment required")
        api_key = self.state.api_key()
        if not api_key:
            return SyncResult("not_registered")
        try:                      # the network call is outside the lock
            client = self._client_factory(s.server_url, self.timeout)
            pulled = client.pull_config(s.pc_id, api_key, self._operator_fn())
        except CentralAuthError as e:
            with self._lock:
                if not self.state.update_if(
                        generation, auth_failed=True, last_sync_at=_now(),
                        last_sync_status=f"auth failed (HTTP {e.status})"):
                    return SyncResult("unavailable", message=_CHANGED)
                self._audit("central_auth_failed", status=e.status)
            log.warning("central rejected this PC's key (HTTP %s); polling "
                        "stopped until re-enrollment", e.status)
            return SyncResult("auth_failed", message=_clean(e))
        except (CentralError, ValueError, OSError) as e:
            return self._unavailable(_clean(e), generation)
        except Exception as e:                      # never let sync crash a thread
            log.warning("unexpected central sync error (%s)", type(e).__name__)
            return self._unavailable(f"{type(e).__name__}: {_clean(e)}",
                                     generation)
        with self._lock:
            if self.state.generation != generation:
                return SyncResult("unavailable", message=_CHANGED)
            if pulled.pc_id != s.pc_id:
                return self._unavailable(
                    "server answered for a different PC", generation)
            result = self._apply(pulled, generation)
        if result.status == "ok":
            self._report(client, s.pc_id, api_key)       # outside the lock: it is a network call
        return result

    def _report(self, client, pc_id: str, api_key: str) -> None:
        """Tell central what this PC runs and how much it did today. Best effort: a failure here
        never changes the sync's outcome (the next poll tries again), and an older server without
        the report simply refuses it."""
        try:
            from core.central_report import build_report
            client.heartbeat(pc_id, api_key, build_report(self.db))
        except Exception as e:
            log.debug("central report not sent (%s)", type(e).__name__)

    def _unavailable(self, message: str, generation: int) -> SyncResult:
        message = _clean(message)
        with self._lock:
            if not self.state.update_if(
                    generation, last_sync_at=_now(),
                    last_sync_status=_clean(f"unavailable: {message}")):
                return SyncResult("unavailable", message=_CHANGED)
        log.warning("central unavailable: %s", message)
        return SyncResult("unavailable", message=message)

    def _apply(self, pulled, generation: int) -> SyncResult:
        """Runs with self._lock held."""
        result = SyncResult("ok")
        managed_before = {r["project_id"] for r in
                          self.db.list_central_projects(managed_only=True)}
        seen: set[str] = set()
        defaults = self._local_seed(managed_before)
        entries = list(pulled.projects)
        for entry in entries[MAX_PROJECTS:]:   # not processed, but still assigned
            if isinstance(entry, dict) and isinstance(
                    entry.get("project_id"), str):
                seen.add(entry["project_id"])
        if len(entries) > MAX_PROJECTS:
            result.errors["overflow"] = (
                f"{len(entries) - MAX_PROJECTS} entries beyond the first "
                f"{MAX_PROJECTS} were skipped")
        for index, entry in enumerate(entries[:MAX_PROJECTS]):
            if self.state.generation != generation:
                return SyncResult("unavailable", message=_CHANGED)
            raw_id = entry.get("project_id") if isinstance(entry, dict) else None
            usable = isinstance(raw_id, str) and bool(raw_id)
            if usable:
                seen.add(raw_id)          # present on the server: not "unassigned"
            key = _clean(raw_id) if usable else f"invalid-entry-{index}"
            try:
                validate_entry(entry)
                self._apply_entry(entry, defaults, result)
            except (PulledProjectError, LocalCopyUnreadable) as e:
                self._skip(result, key, str(e))
            except ProjectConfigError as e:     # its text quotes the value
                path = config_error_path(e)
                self._skip(result, key, "invalid project config"
                           + (f" at {path}" if path else ""))
            except Exception as e:        # one bad project must not stop the rest
                # type only: exception text can quote stored documents
                self._skip(result, key, type(e).__name__)
        for pid in sorted(managed_before - seen):
            self.db.unmanage_central_project(pid)
            result.unassigned.append(pid)
            self._audit("central_project_unassigned", project_id=_clean(pid))
        status = "ok" if not result.errors else \
            f"ok with {len(result.errors)} skipped"
        if not self.state.update_if(
                generation, poll_interval_seconds=pulled.poll_interval_seconds,
                last_sync_at=_now(), last_sync_status=status,
                auth_failed=False):
            return SyncResult("unavailable", message=_CHANGED)
        return result

    def _local_seed(self, managed: set[str]) -> dict:
        """Scanner/export/delivery for a project new to this PC: those of the
        project the pipeline used before enrollment (the first active project
        by name that central does not manage), so scans keep going where they
        went. Without such a project: the app defaults."""
        for row in self.db.list_projects("active"):
            if row["project_id"] in managed:
                continue
            cfg = load_config(row)
            if cfg is None:             # already logged (id only)
                continue
            try:
                return {k: copy.deepcopy(cfg[k]) for k in LOCAL_ONLY_KEYS}
            except KeyError:
                log.warning("project %s unusable as a local-settings seed",
                            _clean(row["project_id"]))
                continue
        return default_local_sections(self.root)

    def _apply_entry(self, entry: dict, defaults: dict,
                     result: SyncResult) -> None:
        pid, version = entry["project_id"], entry["version"]
        rec = self.db.get_central_project(pid)
        if rec is not None and rec["managed"] and \
                rec["central_version"] == version:
            result.unchanged += 1
            return
        row = self.db.get_project(pid)
        local = None
        if row is not None:
            local = load_config(row)
            if local is None:
                # Never overwrite (or re-seed over) a copy we cannot read:
                # that would silently replace this PC's own settings.
                raise LocalCopyUnreadable(UNREADABLE_LOCAL_REASON)
        cfg = overlay(entry, local, defaults)
        try:
            self.pm.adopt(cfg)
        except ValueError:      # the local project FILE is not valid JSON
            raise LocalCopyUnreadable(UNREADABLE_FILE_REASON) from None
        self.db.set_central_project(pid, version, managed=True)
        result.applied.append(pid)
        self._audit("central_project_applied", project_id=pid,
                    central_version=version)

    def _skip(self, result: SyncResult, key: str, reason: str) -> None:
        reason = _clean(reason)
        result.errors[key] = reason
        self._audit("central_project_skipped", project_id=key, reason=reason)
        log.warning("skipped central project %s: %s", key, reason)


# -- background polling --------------------------------------------------------
RETRY_AFTER_FAILURE = 120.0
STARTUP_TIMEOUT = 5.0
STOP_JOIN_TIMEOUT = 5.0


class SyncWorker:
    """Polls central on a daemon thread. Never raises into the app.

    on_result runs on the worker thread: hand it a thread-safe sink such as a
    Qt signal's emit."""

    def __init__(self, service: CentralSyncService, state: CentralState,
                 on_result: Callable[[SyncResult], None] | None = None,
                 join_timeout: float = STOP_JOIN_TIMEOUT):
        self._service, self._state, self._on_result = service, state, on_result
        self._join_timeout = join_timeout
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
    def start(self, initial_sync: bool = True,
              first_result: SyncResult | None = None) -> None:
        """initial_sync=False means a sync already ran (e.g. at startup);
        pass its result as first_result so an unavailable server is retried
        after RETRY_AFTER_FAILURE instead of after the whole poll interval."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, args=(initial_sync, first_result),
            name="central-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Ask the thread to end and wait a bounded time. A sync blocked on
        the network is left to the daemon thread; exit is never held up."""
        self._stop.set()
        self._wake.set()
        t = self._thread
        if t is not None:
            t.join(timeout=self._join_timeout)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def sync_now(self) -> None:
        self._wake.set()

    # -- loop ------------------------------------------------------------------------
    def _run(self, initial_sync: bool,
             first_result: SyncResult | None = None) -> None:
        result = self._do_sync() if initial_sync else first_result
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
                                message=_clean(f"{type(e).__name__}: {e}"))
        if self._on_result is not None:
            try:
                self._on_result(result)
            except Exception:
                log.exception("central sync result handler failed")
        return result


def startup_sync(service: CentralSyncService,
                 state: CentralState) -> SyncResult | None:
    """One short sync before the pipeline profile is chosen. Never raises:
    None when unregistered or if it crashed. The 5 s limit applies to each
    socket operation (connect/read); resolving the host name is not covered
    by it, so a slow DNS lookup can delay startup longer."""
    if not state.is_registered():
        return None
    previous = service.timeout
    service.timeout = STARTUP_TIMEOUT
    try:
        return service.sync_once()
    except Exception:
        log.exception("startup central sync failed; continuing")
        return None
    finally:
        service.timeout = previous


def choose_pipeline_profile(db, fallback: Callable[[], dict]) -> dict:
    """The startup profile. With the per-batch resolver it only feeds the
    pipeline's default runtime and the Admin burn tab's channel; every batch
    resolves its own project. A PC that is managed centrally uses its first
    active managed project; otherwise (or if none is usable) the existing
    first-run behaviour applies."""
    managed = {r["project_id"] for r in
               db.list_central_projects(managed_only=True)}
    for row in db.list_projects("active"):
        if row["project_id"] in managed:
            cfg = load_config(row)
            if cfg is not None:
                return cfg
    return fallback()


def build_central(db, pm: ProfileManager, root, audit, bus,
                  operator_fn: Callable[[], str]):
    """Create the central sync objects: (state, service, worker). Central
    must never stop the application from starting: on any failure log it and
    return (None, None, None); the app then runs without central sync."""
    try:
        state = CentralState(root)
        service = CentralSyncService(db, pm, state, root, audit=audit,
                                     operator_fn=operator_fn)
        worker = SyncWorker(service, state,
                            on_result=bus.central_synced.emit)
        _attach_document_sync(db, state, service)
        return state, service, worker
    except Exception:
        logging.exception("central sync disabled this session")
        return None, None, None


def _attach_document_sync(db, state: CentralState, service: CentralSyncService) -> None:
    """Documents are queued for reporting only while this PC is enrolled; the sender hangs off the service as
    `document_worker` (started by start_document_sync). Failing to set it up never stops the application."""
    try:
        from core.central_documents import DocumentPusher, DocumentSyncWorker
        db.outbox_gate = state.is_registered
        service.document_worker = DocumentSyncWorker(DocumentPusher(db, state))
    except Exception:
        logging.exception("document reporting disabled this session")
        service.document_worker = None


def start_document_sync(service: CentralSyncService | None) -> None:
    worker = getattr(service, "document_worker", None)
    if worker is not None:
        worker.start()


def shutdown_central(worker: SyncWorker | None, service: CentralSyncService | None = None) -> None:
    """Exit path: stop the sync threads if there are any."""
    docs = getattr(service, "document_worker", None)
    if docs is not None:
        docs.stop()
    if worker is not None:
        worker.stop()
