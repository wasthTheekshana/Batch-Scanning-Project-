"""Per-project runtime for the pipeline (design spec 2026-10-12).

A ProjectRuntime is the immutable bundle a batch needs: the project's stored
config plus its delivery channels. RuntimeResolver builds it from the
`projects` table and caches it by (version, updated_at), so an unchanged
project keeps the SAME runtime (and channel objects) across batches while a
Designer save or central sync yields a fresh one for the next batch.
"""
from __future__ import annotations

import copy
import json
import logging
import threading
from dataclasses import dataclass

import jsonschema

from projects.profile_manager import ProfileManager, ProjectConfigError
from delivery.registry import active_channels

log = logging.getLogger(__name__)


class ProjectUnavailable(Exception):
    """The project cannot be used for processing (never silently substituted).

    The message is value-free (field path / exception type only). Callers must
    not log the chained cause (``__cause__``): it can contain config values.
    """


@dataclass(frozen=True)
class ProjectRuntime:
    project_id: str
    profile: dict
    channels: list
    trigger_pool: dict


class RuntimeResolver:
    def __init__(self, db):
        self.db = db
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[tuple, ProjectRuntime]] = {}

    def resolve(self, project_id: str) -> ProjectRuntime:
        row = self.db.get_project(project_id)
        if row is None:
            raise ProjectUnavailable(f"project {project_id!r} not found")
        stamp = (row["version"], row["updated_at"])
        with self._lock:
            cached = self._cache.get(project_id)
            if cached is not None and cached[0] == stamp:
                return cached[1]
            runtime = self._build(project_id, row)       # inside the lock: one object
            self._cache[project_id] = (stamp, runtime)
            return runtime

    @staticmethod
    def _build(project_id: str, row) -> ProjectRuntime:
        try:
            profile = json.loads(row["config_json"])
        except (TypeError, ValueError) as e:
            raise ProjectUnavailable(
                f"project {project_id!r}: stored config is unreadable") from e
        try:
            ProfileManager.validate(profile)
        except ProjectConfigError as e:
            cause = e.__cause__
            path = ("/".join(map(str, cause.absolute_path))
                    if isinstance(cause, jsonschema.ValidationError) else "")
            where = f" at {path}" if path else ""
            raise ProjectUnavailable(
                f"project {project_id!r}: stored config is invalid{where}") from e
        try:
            channels = active_channels(profile.get("delivery", {}))
        except Exception as e:
            raise ProjectUnavailable(
                f"project {project_id!r}: delivery channels unavailable "
                f"({type(e).__name__})") from e
        return ProjectRuntime(project_id=project_id,
                              profile=copy.deepcopy(profile), channels=channels,
                              trigger_pool={c.name: c for c in channels})
