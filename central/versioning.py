"""Append-only config versioning shared by projects and templates."""
import json

from central.models import ConfigVersion


def save_version(db, kind: str, row, config: dict, actor: str) -> bool:
    """Store config on `row` (a Project or Template). Creates a new version
    only if the canonical JSON differs; returns whether it did."""
    text = json.dumps(config, sort_keys=True, separators=(",", ":"))
    if row.current_version > 0 and row.config_json == text:
        return False
    row.config_json = text
    row.current_version += 1
    db.add(ConfigVersion(kind=kind, owner_id=row.id,
                         version=row.current_version, config_json=text,
                         created_by=actor))
    return True
