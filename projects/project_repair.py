"""Repair a project whose stored settings cannot be read.

The database row of a project can be damaged while its settings file, or an
archived earlier version of it, is fine. A repair takes the best intact copy,
in this order: the current settings file, then the newest valid archived
version, then (only as a last resort, and only when the person says so) the
default settings under the same name. Nothing is deleted: the damaged text is
kept in a side file next to the project files.
"""
from __future__ import annotations

import copy
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from projects.profile_manager import DEFAULT_PROJECT, ProfileManager
from projects.project_store import load_config

log = logging.getLogger(__name__)


def unreadable_rows(db) -> list:
    """Active project rows whose stored settings cannot be read."""
    return [row for row in db.list_projects("active") if load_config(row) is None]


def _valid(cfg) -> bool:
    try:
        ProfileManager.validate(cfg)
        return True
    except Exception:
        return False


def _read(path: Path):
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return cfg if isinstance(cfg, dict) and _valid(cfg) else None


def find_intact_copy(pm: ProfileManager, project_id: str):
    """(settings, where it came from) of the best intact copy, or None."""
    cfg = _read(pm._path(project_id))
    if cfg is not None and cfg.get("project_id") == project_id:
        return cfg, "the current settings file"
    folder = pm.archive_dir / project_id
    if folder.is_dir():
        versions = sorted((p for p in folder.glob("v*.json") if p.stem[1:].isdigit()),
                          key=lambda p: int(p.stem[1:]), reverse=True)
        for path in versions:
            cfg = _read(path)
            if cfg is not None and cfg.get("project_id") == project_id:
                return cfg, f"saved version {int(path.stem[1:])}"
    return None


def describe(pm: ProfileManager, project_id: str) -> str:
    found = find_intact_copy(pm, project_id)
    if found:
        return f"can be restored from {found[1]}"
    return "no intact copy exists: only the default settings can be put back (its own settings are lost)"


def _keep_damaged_text(pm: ProfileManager, row) -> Path | None:
    try:
        folder = pm.projects_dir / "damaged"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        path = folder / f"{row['project_id']}-{stamp}.txt"
        path.write_text(str(row["config_json"]), encoding="utf-8")
        return path
    except Exception:
        log.warning("could not keep the damaged settings of a project")
        return None


def repair(pm: ProfileManager, db, project_id: str, allow_default: bool = False) -> dict:
    """Restore a project. Returns {"source", "name", "version", "damaged_copy"}.
    Raises LookupError for an unknown project and ValueError when no intact copy
    exists and allow_default is False."""
    row = db.get_project(project_id)
    if row is None:
        raise LookupError("unknown project")
    found = find_intact_copy(pm, project_id)
    if found is not None:
        cfg, source = copy.deepcopy(found[0]), found[1]
    elif allow_default:
        cfg = copy.deepcopy(DEFAULT_PROJECT)
        cfg["project_id"] = project_id
        cfg["name"] = row["name"]
        cfg["created"] = datetime.now(timezone.utc).isoformat()
        cfg["rules"]["rule_set_id"] = cfg["rules"].get("rule_set_id") or project_id
        pm._fill_blank_paths(cfg)
        ProfileManager.validate(cfg)
        source = "the default settings"
    else:
        raise ValueError("no intact copy of this project exists")
    damaged = _keep_damaged_text(pm, row)
    cfg["version"] = max(int(row["version"] or 1), int(cfg.get("version", 1))) + 1
    cfg["status"] = "active"
    pm._write(cfg)
    db.update_project(project_id, cfg["name"], cfg, cfg["version"], "active")
    return {"source": source, "name": cfg["name"], "version": cfg["version"],
            "damaged_copy": str(damaged) if damaged else None}
