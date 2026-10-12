"""Merge a project pulled from central with this PC's local-only sections.

Mirrors _LIVE_ONLY_TOP_KEYS in core/template_manager.py: central owns the
processing settings; the PC owns where it scans from and where output goes.
Nothing under LOCAL_ONLY_KEYS is ever taken from a pulled config.
"""
from __future__ import annotations

import copy
from pathlib import Path

from core.profile_manager import DEFAULT_PROJECT, is_safe_project_id
from core.safe_paths import is_safe_file_name, is_safe_relative_subpath

LOCAL_ONLY_KEYS = ("scanner", "export", "delivery")


class PulledProjectError(ValueError):
    """A pulled project entry is unusable; the others may still apply."""


def validate_entry(entry) -> None:
    if not isinstance(entry, dict):
        raise PulledProjectError("project entry is not an object")
    pid = entry.get("project_id")
    if not is_safe_project_id(pid):
        raise PulledProjectError(f"unsafe project_id: {pid!r}")
    version = entry.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise PulledProjectError(f"invalid version: {version!r}")
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PulledProjectError("missing project name")
    config = entry.get("config")
    if not isinstance(config, dict):
        raise PulledProjectError("config is not an object")
    if config.get("project_id") not in (None, pid):
        raise PulledProjectError("config.project_id does not match the entry")
    _check_rule_actions(config)


def _check_rule_actions(config: dict) -> None:
    """Reject rules whose set_folder / set_filename values could write outside
    the output root (the exporter and channels guard again at write time)."""
    rules_cfg = config.get("rules")
    rules = rules_cfg.get("rules") if isinstance(rules_cfg, dict) else None
    if not isinstance(rules, list):
        return
    for i, rule in enumerate(rules):
        actions = rule.get("actions") if isinstance(rule, dict) else None
        if not isinstance(actions, list):
            continue
        for j, action in enumerate(actions):
            if not isinstance(action, dict):
                continue
            kind, value = action.get("type"), action.get("value")
            where = f"rule {i} action {j}"
            if kind == "set_folder" and not is_safe_relative_subpath(value):
                raise PulledProjectError(
                    f"unsafe set_folder value in {where}: {value!r}")
            if kind == "set_filename" and not is_safe_file_name(value):
                raise PulledProjectError(
                    f"unsafe set_filename value in {where}: {value!r}")


def default_local_sections(root: Path) -> dict:
    """Local defaults for a project this PC has never had: same paths as
    main.build_default_project uses for the first-run project."""
    root = Path(root)
    base = copy.deepcopy(DEFAULT_PROJECT)
    sections = {k: base[k] for k in LOCAL_ONLY_KEYS}
    sections["export"]["output_path"] = str(root / "output")
    sections["delivery"]["local_folder"]["path"] = str(root / "delivered")
    return sections


def overlay(entry: dict, local: dict | None, defaults: dict) -> dict:
    """entry must already have passed validate_entry."""
    cfg = copy.deepcopy(entry["config"])
    cfg["project_id"] = entry["project_id"]
    cfg["name"] = entry["name"]
    source = local if local is not None else defaults
    for key in LOCAL_ONLY_KEYS:
        cfg[key] = copy.deepcopy(source.get(key, defaults[key]))
    return cfg
