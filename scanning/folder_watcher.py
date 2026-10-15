"""Hot-folder watcher (HLD §4 Layer 1).

Polls the watch directory and fires a callback for each new file once
its size is stable across two polls (a file still being copied by the
MFP/network scanner grows between polls). stdlib-only so the packaged
EXE has no extra native dependencies; the poll interval is far below
scanner copy latency.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

POLL_SECONDS = 2.0


class FolderWatchSource:
    def __init__(self, watch_dir: str | Path,
                 callback: Callable[[Path], None],
                 poll_seconds: float = POLL_SECONDS,
                 extensions: set[str] | None = None,
                 process_existing: bool = False):
        self.watch_dir = Path(watch_dir)
        self.callback = callback
        self.poll_seconds = poll_seconds
        self.process_existing = process_existing
        self.extensions = extensions or {".pdf", ".tif", ".tiff", ".jpg",
                                         ".jpeg", ".png", ".bmp"}
        self._known: dict[Path, int] = {}
        self._pending: dict[Path, int] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.watch_dir.mkdir(parents=True, exist_ok=True)
        # existing files are treated as already processed, unless the caller
        # moves files away once handled (then anything still here is new)
        self._known = ({} if self.process_existing else
                       {p: p.stat().st_size for p in self._iter_files()})
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="hot-folder")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.poll_seconds * 3)
            self._thread = None

    def poll_once(self) -> list[Path]:
        """One poll cycle; returns files dispatched this cycle.
        Exposed for tests and manual triggering."""
        dispatched = []
        for path in self._iter_files():
            if path in self._known:
                continue
            size = path.stat().st_size
            if self._pending.get(path) == size:
                self._known[path] = size
                del self._pending[path]
                dispatched.append(path)
                try:
                    self.callback(path)
                except Exception:
                    log.exception("hot folder callback failed for %s", path)
            else:
                self._pending[path] = size  # wait until size is stable
        return dispatched

    def _iter_files(self):
        return (p for p in self.watch_dir.iterdir()
                if p.is_file() and p.suffix.lower() in self.extensions)

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            try:
                self.poll_once()
            except Exception:
                log.exception("hot folder poll failed")
