"""Apply the projects central assigned to this PC (spec: sync client).

CentralSyncService.sync_once() is safe to call from any thread and never
raises: every outcome is a SyncResult. It never touches the running pipeline
- new or changed projects are written to the local project store/database and
take effect for the pipeline at the next application start (restart_needed).

Everything that comes from the server (project ids, exception text) is
untrusted: it is stripped of control characters and truncated before it
reaches an audit entry, a log line or a SyncResult.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core.central_client import (CentralAuthError, CentralClient,
                                 CentralError, allow_loopback_http)
from core.central_state import CentralState
from core.config_overlay import (PulledProjectError, default_local_sections,
                                 overlay, validate_entry)
from core.profile_manager import ProfileManager, ProjectConfigError

log = logging.getLogger(__name__)

ACTOR = "central-sync"
MAX_PROJECTS = 500          # entries processed per pull; the rest are skipped
MAX_TEXT = 200
_CHANGED = "registration changed during sync"


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
        self._audit("central_registered", pc_id=_clean(result.pc_id),
                    server=_clean(url))

    def disconnect(self) -> None:
        """Forget the registration; keep every local project and its data."""
        self.db.unmanage_all_central_projects()
        self.state.clear()
        self._audit("central_disconnected")

    # -- the sync itself ---------------------------------------------------------
    def _identity(self) -> tuple:
        s = self.state.settings
        return (s.pc_id, self.state.api_key(), s.server_url)

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
        identity = (s.pc_id, api_key, s.server_url)
        try:
            client = self._client_factory(s.server_url, self.timeout)
            pulled = client.pull_config(s.pc_id, api_key, self._operator_fn())
        except CentralAuthError as e:
            if self._identity() != identity:        # outcome is for an old key
                return SyncResult("unavailable", message=_CHANGED)
            self.state.update(auth_failed=True, last_sync_at=_now(),
                              last_sync_status=f"auth failed (HTTP {e.status})")
            self._audit("central_auth_failed", status=e.status)
            log.warning("central rejected this PC's key (HTTP %s); polling "
                        "stopped until re-enrollment", e.status)
            return SyncResult("auth_failed", message=_clean(e))
        except (CentralError, ValueError, OSError) as e:
            return self._unavailable(_clean(e), identity)
        except Exception as e:                      # never let sync crash a thread
            log.warning("unexpected central sync error (%s)", type(e).__name__)
            return self._unavailable(f"{type(e).__name__}: {_clean(e)}",
                                     identity)
        if self._identity() != identity:
            return SyncResult("unavailable", message=_CHANGED)
        if pulled.pc_id != identity[0]:
            return self._unavailable("server answered for a different PC",
                                     identity)
        return self._apply(pulled, identity)

    def _unavailable(self, message: str, identity: tuple) -> SyncResult:
        message = _clean(message)
        if self._identity() != identity:
            return SyncResult("unavailable", message=_CHANGED)
        log.warning("central unavailable: %s", message)
        self.state.update(last_sync_at=_now(),
                          last_sync_status=_clean(f"unavailable: {message}"))
        return SyncResult("unavailable", message=message)

    def _apply(self, pulled, identity: tuple) -> SyncResult:
        result = SyncResult("ok")
        managed_before = {r["project_id"] for r in
                          self.db.list_central_projects(managed_only=True)}
        seen: set[str] = set()
        defaults = default_local_sections(self.root)
        entries = list(pulled.projects)
        for entry in entries[MAX_PROJECTS:]:   # not processed, but still assigned
            if isinstance(entry, dict) and isinstance(
                    entry.get("project_id"), str):
                seen.add(entry["project_id"])
        if len(entries) > MAX_PROJECTS:
            result.errors["overflow"] = (
                f"{len(entries) - MAX_PROJECTS} entries beyond the first "
                f"{MAX_PROJECTS} were skipped")
        for entry in entries[:MAX_PROJECTS]:
            raw_id = entry.get("project_id") if isinstance(entry, dict) else None
            if isinstance(raw_id, str):
                seen.add(raw_id)          # present on the server: not "unassigned"
            try:
                validate_entry(entry)
                self._apply_entry(entry, defaults, result)
            except (PulledProjectError, ProjectConfigError) as e:
                self._skip(result, raw_id, str(e))
            except Exception as e:        # one bad project must not stop the rest
                self._skip(result, raw_id, f"{type(e).__name__}: {e}")
        for pid in sorted(managed_before - seen):
            self.db.unmanage_central_project(pid)
            result.unassigned.append(pid)
            self._audit("central_project_unassigned", project_id=_clean(pid))
        if self._identity() != identity:   # do not resurrect a cleared state
            result.message = _CHANGED
            return result
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
        key, reason = _clean(raw_id), _clean(reason)
        result.errors[key] = reason
        self._audit("central_project_skipped", project_id=key, reason=reason)
        log.warning("skipped central project %s: %s", key, reason)
