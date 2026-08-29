"""Project templates - reusable starter configs (Enhancement Plan §4.4,
generalized beyond banking to any document/project kind).

A template is a *partial* project config - only the reusable parts
(index zones, separation strategy, QC/enhancement settings, features,
rules), never a project's identity or delivery/export destinations.
Built-in templates ship as JSON under core/templates/ and are seeded
into the DB on app startup; admins can also capture any of their own
configured projects as a new template via save_as_template().
"""
from __future__ import annotations

import copy
import json
import logging
from pathlib import Path

from core.models import new_id
from core.profile_manager import DEFAULT_PROJECT

log = logging.getLogger(__name__)

# Top-level project-config keys that describe a live project's identity
# or its delivery destination - never captured into a reusable template.
_LIVE_ONLY_TOP_KEYS = {"project_id", "name", "version", "cloned_from",
                       "status", "created", "scanner", "export", "delivery"}


class TemplateManager:
    def __init__(self, db):
        self.db = db

    def _to_dict(self, row) -> dict:
        d = dict(row)
        d["config"] = json.loads(d.pop("config_json"))
        d["built_in"] = bool(d["built_in"])
        return d

    def list_templates(self) -> list[dict]:
        return [self._to_dict(row) for row in self.db.list_templates()]

    def get(self, template_id: str) -> dict:
        row = self.db.get_template(template_id)
        return self._to_dict(row)

    def save_as_template(self, project_config: dict, name: str,
                         category: str) -> dict:
        """Capture the reusable parts of an existing (or in-progress)
        project config as a new admin-saved template."""
        partial = {k: v for k, v in project_config.items()
                  if k not in _LIVE_ONLY_TOP_KEYS}
        template_id = new_id()
        self.db.insert_template(template_id, name, category, partial,
                                built_in=False)
        return self.get(template_id)

    def build_project_config(self, template_id: str) -> dict:
        """A ready-to-create project config: the template's fields
        override DEFAULT_PROJECT's, so every schema-required key
        (export.output_path, delivery, scanner, ...) is present even
        though the template itself never stores them."""
        template = self.get(template_id)
        cfg = copy.deepcopy(DEFAULT_PROJECT)
        cfg.update(template["config"])
        return cfg

    def seed_built_ins(self, templates_dir: Path | None = None) -> None:
        """Insert or update built-in templates from core/templates/*.json.
        Never touches an admin-saved template, even one with the same
        name as a built-in - matching is scoped to built_in=1 rows only."""
        templates_dir = templates_dir or (Path(__file__).parent / "templates")
        if not templates_dir.is_dir():
            return
        existing_built_ins = {t["name"]: t for t in self.list_templates()
                              if t["built_in"]}
        for path in sorted(templates_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                log.warning("skipping malformed built-in template %s: %s",
                           path, e)
                continue
            name = data["name"]
            category = data["category"]
            config = data["config"]
            current = existing_built_ins.get(name)
            if current is None:
                self.db.insert_template(new_id(), name, category, config,
                                        built_in=True)
            elif current["config"] != config:
                self.db.update_template_config(current["template_id"], config)
