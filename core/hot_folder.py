"""Hot folders: a network scanner or MFP drops PDFs/TIFFs/images into a
folder and each file becomes a batch of its project, with no operator.

A project turns it on with `scanner.hot_folder = {"enabled": true, "path": ...}`.
Each file is imported, submitted as a batch (operator "hot-folder") and then
moved to `processed/` inside the watched folder, or to `failed/` when it could
not be imported or the project is unavailable, so nothing is handled twice and
nothing is lost. Sub-folders are never watched.
"""
from __future__ import annotations

import logging
import shutil
import threading
import uuid
from pathlib import Path

from core.project_store import readable_projects
from scanning.file_import import import_files
from scanning.folder_watcher import FolderWatchSource

log = logging.getLogger(__name__)

OPERATOR = "hot-folder"
MAX_FILE_BYTES = 500 * 1024 * 1024      # a larger file goes to failed/ unread
RELOAD_SECONDS = 30.0


class HotFolderService:
    def __init__(self, db, pipeline, staging_dir, audit=None,
                 poll_seconds: float = 2.0,
                 reload_seconds: float = RELOAD_SECONDS):
        self.db = db
        self.pipeline = pipeline
        self.staging_dir = Path(staging_dir)
        self.audit = audit
        self.poll_seconds = poll_seconds
        self.reload_seconds = reload_seconds
        self._watchers: dict[Path, FolderWatchSource] = {}
        self._projects: dict[Path, str] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- which folders --------------------------------------------------------
    def wanted(self) -> dict[Path, str]:
        """{folder: project_id} for every active, readable project that has a
        hot folder switched on. The first project wins a shared folder."""
        found: dict[Path, str] = {}
        readable, _bad = readable_projects(self.db.list_projects("active"))
        for row, cfg in readable:
            hot = (cfg.get("scanner") or {}).get("hot_folder") or {}
            raw = str(hot.get("path") or "").strip()
            if not hot.get("enabled") or not raw:
                continue
            folder = Path(raw)
            if folder in found:
                log.warning("hot folder %s is used by two projects; "
                            "only the first one receives files", folder)
                continue
            found[folder] = row["project_id"]
        return found

    def reload(self) -> None:
        """Start watching new folders and stop watching removed ones."""
        wanted = self.wanted()
        for folder in list(self._watchers):
            if wanted.get(folder) != self._projects.get(folder):
                self._watchers.pop(folder).stop()
                self._projects.pop(folder, None)
        for folder, project_id in wanted.items():
            if folder in self._watchers:
                continue
            watcher = FolderWatchSource(
                folder, lambda path, p=project_id: self.handle(p, path),
                poll_seconds=self.poll_seconds, process_existing=True)
            try:
                watcher.start()
            except OSError as e:
                log.warning("hot folder %s cannot be watched: %s", folder, e)
                continue
            self._watchers[folder] = watcher
            self._projects[folder] = project_id

    # -- one file -------------------------------------------------------------
    def handle(self, project_id: str, path: Path) -> str | None:
        """Turn one dropped file into a batch. Returns the batch id, or None
        when the file went to failed/."""
        batch_id = str(uuid.uuid4())
        try:
            self._check_file(path)
            jobs = import_files([path], batch_id, project_id, self.staging_dir)
            if not jobs:
                raise ValueError("no pages could be imported")
            self.pipeline.submit_batch(batch_id, jobs, operator=OPERATOR)
        except Exception as e:
            # the type only: the message can carry configuration values
            log.warning("hot folder file %s not processed: %s",
                        path.name, type(e).__name__)
            if self.audit is not None:
                self.audit.log("hot_folder_failed", OPERATOR,
                               project_id=project_id, error=type(e).__name__)
            self._discard_staged(batch_id)
            self._move(path, "failed", batch_id)
            return None
        if self.audit is not None:
            self.audit.log("hot_folder_batch", OPERATOR, batch_id=batch_id,
                           project_id=project_id, pages=len(jobs))
        self._move(path, "processed", batch_id)
        return batch_id

    @staticmethod
    def _check_file(path: Path) -> None:
        """Only a plain file that really lives in the watched folder: a link
        (symlink or junction) could point at a file elsewhere on the PC, and
        an enormous file would fill the staging disk."""
        if path.is_symlink() or path.resolve().parent != path.parent.resolve():
            raise ValueError("not a plain file in the hot folder")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("file too large")

    def _discard_staged(self, batch_id: str) -> None:
        """Page images of a batch that was never submitted would stay in
        staging for good."""
        for page in self.staging_dir.glob(f"{batch_id}_*"):
            try:
                page.unlink()
            except OSError:
                pass

    @staticmethod
    def _move(path: Path, folder: str, batch_id: str) -> None:
        target_dir = path.parent / folder
        try:
            target_dir.mkdir(exist_ok=True)
            target = target_dir / path.name
            if target.exists():
                target = target_dir / f"{batch_id[:8]}_{path.name}"
            shutil.move(str(path), str(target))
        except OSError as e:
            log.warning("could not move %s to %s/: %s", path.name, folder,
                        type(e).__name__)

    # -- running ------------------------------------------------------------------
    def start(self) -> None:
        self.reload()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="hot-folder-reload")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        for watcher in self._watchers.values():
            watcher.stop()
        self._watchers.clear()
        self._projects.clear()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(self.reload_seconds):
            try:
                self.reload()
            except Exception:
                log.exception("hot folder reload failed")
