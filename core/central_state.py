"""Local state for the central-sync feature: non-secret settings in
central.json plus the DPAPI-protected API key (spec: sync client)."""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from data.keystore import ProtectedBlob

log = logging.getLogger(__name__)

SETTINGS_FILE = "central.json"
KEY_FILE = "central_api_key.bin"
MIN_POLL, MAX_POLL, DEFAULT_POLL = 30, 86400, 600


def clamp_poll(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_POLL
    return max(MIN_POLL, min(MAX_POLL, value))


@dataclass
class CentralSettings:
    server_url: str = ""
    pc_id: str = ""
    poll_interval_seconds: int = DEFAULT_POLL
    auth_failed: bool = False
    last_sync_at: str = ""
    last_sync_status: str = ""


class CentralState:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._path = self.root / SETTINGS_FILE
        self._key = ProtectedBlob(self.root / "keys" / KEY_FILE,
                                  "DOKScanner central API key")
        self._lock = threading.Lock()  # worker thread + UI both write
        self.settings = self._load()

    def _load(self) -> CentralSettings:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except FileNotFoundError:
            return CentralSettings()
        except (OSError, ValueError) as e:
            log.warning("ignoring unreadable %s: %s", self._path, e)
            return CentralSettings()
        known = {f.name for f in fields(CentralSettings)}
        try:
            s = CentralSettings(**{k: v for k, v in data.items() if k in known})
        except TypeError:
            return CentralSettings()
        s.poll_interval_seconds = clamp_poll(s.poll_interval_seconds)
        return s

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self.settings), indent=2),
                       encoding="utf-8")
        os.replace(tmp, self._path)

    def is_registered(self) -> bool:
        s = self.settings
        return bool(s.server_url and s.pc_id and self._key.exists())

    def api_key(self) -> str | None:
        try:
            raw = self._key.load()
        except RuntimeError as e:
            log.error("cannot read the central API key: %s", e)
            return None
        return raw.decode("utf-8") if raw else None

    def register_result(self, server_url: str, pc_id: str, api_key: str,
                        poll_interval_seconds: int) -> None:
        with self._lock:
            self._key.save(api_key.encode("utf-8"))
            self.settings = CentralSettings(
                server_url=server_url, pc_id=pc_id,
                poll_interval_seconds=clamp_poll(poll_interval_seconds))
            self._save()

    def update(self, **changes) -> None:
        with self._lock:
            known = {f.name for f in fields(CentralSettings)}
            for k, v in changes.items():
                if k not in known:
                    raise AttributeError(f"unknown central setting: {k}")
                setattr(self.settings, k, v)
            self.settings.poll_interval_seconds = clamp_poll(
                self.settings.poll_interval_seconds)
            self._save()

    def clear(self) -> None:
        with self._lock:
            self._key.delete()
            self.settings = CentralSettings()
            self._path.unlink(missing_ok=True)
