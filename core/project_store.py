"""Safe reading of stored project / template configs.

A row's config_json can be corrupt (malformed JSON, or valid JSON that is not
an object). Callers use load_config() instead of a bare json.loads so one bad
row never crashes startup, the wizard, the Index tab or central sync.

Security: only the row id and fixed text are logged - never the stored content
and never the JSONDecodeError text (it quotes the document).
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

_ID_COLUMNS = {"project": "project_id", "template": "template_id"}


def _row_id(row, kind: str) -> str:
    try:
        return str(row[_ID_COLUMNS.get(kind, "project_id")])
    except (KeyError, IndexError, TypeError):
        return "?"


def load_config(row, kind: str = "project") -> dict | None:
    """The row's parsed config_json, or None when it is unreadable JSON or not
    a JSON object. Logs one warning with the row id only."""
    try:
        cfg = json.loads(row["config_json"])
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        log.warning("stored %s %s has an unreadable config (%s)", kind,
                    _row_id(row, kind), type(exc).__name__)
        return None
    if not isinstance(cfg, dict):
        log.warning("stored %s %s has an unreadable config (not an object)",
                    kind, _row_id(row, kind))
        return None
    return cfg


def readable_projects(rows) -> tuple[list[tuple[object, dict]], list[str]]:
    """Split project rows into [(row, config)] for the readable ones and the
    NAMES of the unreadable ones (names are not secrets)."""
    good: list[tuple[object, dict]] = []
    bad: list[str] = []
    for row in rows:
        cfg = load_config(row)
        if cfg is None:
            try:
                bad.append(str(row["name"]))
            except (KeyError, IndexError, TypeError):
                bad.append(_row_id(row, "project"))
        else:
            good.append((row, cfg))
    return good, bad
