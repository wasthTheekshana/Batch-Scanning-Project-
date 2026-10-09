"""Project configuration system (HLD §6).

A project is a versioned JSON file that completely describes how documents
are processed. Saves increment the version and archive the previous JSON;
clones get a fresh uuid; any archived version can be rolled back to.
"""
from __future__ import annotations

import copy
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

_SAFE_ID = re.compile(r"[A-Za-z0-9-]{1,36}")
_SCHEMA_PATH = Path(__file__).parent / "project_schema.json"
_SCHEMA = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))

DEFAULT_PROJECT: dict = {
    "project_id": "",
    "name": "",
    "version": 1,
    "cloned_from": None,
    "status": "active",
    "created": "",
    "scanner": {"dpi": 300, "color": "grayscale", "duplex": True, "sdk": "file"},
    "enhancement_profile": {
        "deskew": True, "crop": True, "clahe": True,
        "despeckle": True, "whiten_bg": True, "target_dpi": 300,
    },
    "separation": {
        "strategy": ["blank_page"],
        "barcode_types": ["QR", "Code128", "Patch"],
        "blank_threshold": 0.02,
        "fixed_pages": None,
        "zones": [],
    },
    "qc": {
        "enabled": True,
        "stages": ["punch_hole", "rotation", "margin", "color", "advanced"],
        "blur_warn_threshold": 80,
        "blur_fail_threshold": 30,
        "dpi_warn": 200,
        "dpi_fail": 150,
        "pass_score": 80,
    },
    "ocr": {"lang": "eng", "zones": [], "confidence_threshold": 0.75},
    "export": {
        "format": "searchable_pdf",
        "pdf_a": False,
        "filename_template": "{date}_{batch_id}_{counter:04d}",
        "output_path": "",
    },
    "delivery": {
        "local_folder": {"enabled": True, "path": ""},
        "sftp": {"enabled": False},
        "cd_dvd": {"enabled": False},
    },
    "features": {
        "cd_burn": False, "sftp": False, "ai_assistant": False,
        "auto_index": True, "duplicate_check": True,
    },
    "rules": {"rule_set_id": "", "version": 1, "rules": []},
}


class ProjectConfigError(Exception):
    """Raised when a project config fails schema validation."""


class ProfileManager:
    def __init__(self, projects_dir: str | Path, db=None):
        self.projects_dir = Path(projects_dir)
        self.archive_dir = self.projects_dir / "archive"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.db = db

    # -- validation ---------------------------------------------------------
    @staticmethod
    def validate(config: dict) -> None:
        try:
            jsonschema.validate(config, _SCHEMA)
        except jsonschema.ValidationError as e:
            raise ProjectConfigError(
                f"invalid project config at {'/'.join(map(str, e.absolute_path))}: "
                f"{e.message}") from e

    # -- operations ----------------------------------------------------------
    def create(self, name: str, config: dict | None = None) -> dict:
        cfg = copy.deepcopy(config or DEFAULT_PROJECT)
        cfg["project_id"] = str(uuid.uuid4())
        cfg["name"] = name
        cfg["version"] = 1
        cfg["created"] = datetime.now(timezone.utc).isoformat()
        if not cfg["rules"].get("rule_set_id"):
            cfg["rules"]["rule_set_id"] = str(uuid.uuid4())
        self.validate(cfg)
        self._write(cfg)
        if self.db:
            self.db.insert_project(cfg["project_id"], name, cfg)
        return cfg

    def save(self, config: dict) -> dict:
        """Increment version, archive the previous file, persist."""
        self.validate(config)
        current = self._path(config["project_id"])
        if current.exists():
            old = json.loads(current.read_text(encoding="utf-8"))
            self._archive(old)
            config = {**config, "version": old["version"] + 1}
        self._write(config)
        if self.db:
            self.db.update_project(config["project_id"], config["name"],
                                   config, config["version"],
                                   config.get("status", "active"))
        return config

    def adopt(self, config: dict) -> dict:
        """Install a config that arrived from elsewhere (central) under its
        own project_id: create it at version 1, or save a new version if the
        project already exists. The id becomes a file name, so it must be
        safe; the incoming 'version' is ignored (local counter)."""
        self.validate(config)
        project_id = config["project_id"]
        if not isinstance(project_id, str) or not _SAFE_ID.fullmatch(project_id):
            raise ProjectConfigError(f"unsafe project_id: {project_id!r}")
        if self._path(project_id).exists():
            return self.save(config)
        cfg = {**config, "version": 1}
        self._write(cfg)
        if self.db:
            if self.db.get_project(project_id) is None:
                self.db.insert_project(project_id, cfg["name"], cfg)
            else:
                self.db.update_project(project_id, cfg["name"], cfg, 1,
                                       cfg.get("status", "active"))
        return cfg

    def clone(self, project_id: str, new_name: str) -> dict:
        parent = self.load(project_id)
        cfg = copy.deepcopy(parent)
        cfg["project_id"] = str(uuid.uuid4())
        cfg["name"] = new_name
        cfg["version"] = 1
        cfg["cloned_from"] = project_id
        cfg["created"] = datetime.now(timezone.utc).isoformat()
        self.validate(cfg)
        self._write(cfg)
        if self.db:
            self.db.insert_project(cfg["project_id"], new_name, cfg,
                                   cloned_from=project_id)
        return cfg

    def rollback(self, project_id: str, version: int) -> dict:
        archived = self.archive_dir / project_id / f"v{version}.json"
        if not archived.exists():
            raise ProjectConfigError(
                f"no archived version {version} for project {project_id}")
        current = self.load(project_id)
        self._archive(current)
        restored = json.loads(archived.read_text(encoding="utf-8"))
        restored["version"] = current["version"] + 1  # rollback is a new save
        self._write(restored)
        if self.db:
            self.db.update_project(project_id, restored["name"], restored,
                                   restored["version"],
                                   restored.get("status", "active"))
        return restored

    def load(self, project_id: str) -> dict:
        path = self._path(project_id)
        if not path.exists():
            raise ProjectConfigError(f"project {project_id} not found")
        cfg = json.loads(path.read_text(encoding="utf-8"))
        self.validate(cfg)
        return cfg

    def list_versions(self, project_id: str) -> list[int]:
        d = self.archive_dir / project_id
        if not d.exists():
            return []
        return sorted(int(p.stem[1:]) for p in d.glob("v*.json"))

    # -- internals -------------------------------------------------------------
    def _path(self, project_id: str) -> Path:
        return self.projects_dir / f"{project_id}.json"

    def _write(self, cfg: dict) -> None:
        self._path(cfg["project_id"]).write_text(
            json.dumps(cfg, indent=2), encoding="utf-8")

    def _archive(self, cfg: dict) -> None:
        d = self.archive_dir / cfg["project_id"]
        d.mkdir(parents=True, exist_ok=True)
        (d / f"v{cfg['version']}.json").write_text(
            json.dumps(cfg, indent=2), encoding="utf-8")
