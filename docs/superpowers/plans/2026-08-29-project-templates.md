# Project Templates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin create a new project pre-filled from a reusable template (5 built-in starters — Banking, Insurance, HR/Personnel, Legal/Contracts, Invoices — plus any admin has saved from their own projects), instead of building every new project's zones/rules from scratch.

**Architecture:** A new `project_templates` DB table + `core/template_manager.py` (`TemplateManager`, mirroring the existing `ProfileManager` pattern) holds reusable *partial* project configs (zones, rules, QC/enhancement settings only — no project identity, no delivery/export paths). Built-in templates ship as JSON files under `core/templates/`, seeded into the DB on app startup. The Workflow Designer gets two new toolbar actions: "New from template…" (merges a template onto `DEFAULT_PROJECT` and calls the existing `ProfileManager.create()`) and "Save as template…" (captures the currently-loaded project's reusable parts).

**Tech Stack:** Python 3.11, SQLite (existing `data/db.py`), PyQt6 (existing `ui/workflow_designer.py` patterns — `QInputDialog.getItem`/`getText`, matching `clone_project`/`rollback_dialog`).

**Spec:** `docs/superpowers/specs/2026-08-29-project-templates-design.md`

## Global Constraints

- A template's stored `config` covers only: `ocr` (zones + regex + lang + confidence_threshold), `separation` (strategy/blank_threshold — not delivery-related), `qc`, `enhancement_profile`, `features`, `rules`. Never `project_id`, `name`, `version`, `cloned_from`, `status`, `created`, `scanner`, `export`, `delivery`.
- Built-in template zones ship with `name` + `regex` only — **no** `x`/`y`/`w`/`h` (position is sample-specific; the admin drags the region on first use, same as any hand-built zone).
- `core/project_schema.json` requires `export.output_path` and the full `delivery` block to exist — a template's partial config must be merged onto `copy.deepcopy(DEFAULT_PROJECT)` before it ever reaches `ProfileManager.create()`/`validate()`, never passed directly.
- Built-in template files live at `core/templates/*.json` (inside the `core` package, not a top-level directory) so packaging reuses the exact mechanism already proven for `core/project_schema.json`.
- Every new function/button gets a test, following the existing test file's conventions (`tests/test_phase2_ui.py`'s `designer` fixture for UI tests, `data/db.py`'s CRUD test style for DB tests).

---

## File Structure

- **Modify:** `data/db.py` — add `project_templates` table to `_SCHEMA`, add a `# -- project templates` CRUD section.
- **Create:** `core/template_manager.py` — `TemplateManager`: `list_templates()`, `get()`, `save_as_template()`, `build_project_config()`, `seed_built_ins()`.
- **Modify:** `ocr/zone_extractor.py` — `extract_fields()` skips a zone missing `x`/`y`/`w`/`h` instead of crashing (needed because a freshly-created from-template project's zones start unpositioned).
- **Create:** `core/templates/banking.json`, `core/templates/insurance.json`, `core/templates/hr.json`, `core/templates/legal.json`, `core/templates/invoices.json`.
- **Modify:** `main.py` — call `TemplateManager(db).seed_built_ins()` once at startup.
- **Modify:** `ui/workflow_designer.py` — add "New from template…" and "Save as template…" toolbar buttons + their handler methods.
- **Modify:** `docscanner.spec` — bundle `core/templates/` via `datas`.
- **Modify:** `README.md` — document the new capability.
- **Test:** `tests/test_templates.py` (new) — DB CRUD, `TemplateManager` behavior, built-in seeding.
- **Test:** `tests/test_ocr.py` (existing — confirm this is where `zone_extractor` is tested before appending) — the unpositioned-zone-skip fix.
- **Test:** `tests/test_phase2_ui.py` (existing) — the two new Workflow Designer buttons.

---

### Task 1: `project_templates` table + CRUD

**Files:**
- Modify: `data/db.py`
- Test: `tests/test_templates.py` (new file)

**Interfaces:**
- Produces: `Database.insert_template(template_id, name, category, config, built_in=False) -> None`, `Database.get_template(template_id) -> sqlite3.Row | None`, `Database.list_templates() -> list[sqlite3.Row]`, `Database.update_template_config(template_id, config) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the project-templates system (Enhancement Plan §4.4)."""
import json

import pytest

from data.db import Database


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "templates.db")
    yield d
    d.close()


def test_insert_and_get_template(db):
    db.insert_template("t1", "Banking", "Banking", {"ocr": {"zones": []}})
    row = db.get_template("t1")
    assert row["name"] == "Banking"
    assert row["category"] == "Banking"
    assert json.loads(row["config_json"]) == {"ocr": {"zones": []}}
    assert row["built_in"] == 0


def test_insert_template_built_in_flag(db):
    db.insert_template("t1", "Banking", "Banking", {}, built_in=True)
    assert db.get_template("t1")["built_in"] == 1


def test_list_templates_ordered_by_category_then_name(db):
    db.insert_template("t1", "Zeta Corp", "Legal", {})
    db.insert_template("t2", "Alpha Bank", "Banking", {})
    db.insert_template("t3", "Beta Co", "Banking", {})
    rows = db.list_templates()
    assert [(r["category"], r["name"]) for r in rows] == [
        ("Banking", "Alpha Bank"), ("Banking", "Beta Co"),
        ("Legal", "Zeta Corp")]


def test_update_template_config(db):
    db.insert_template("t1", "Banking", "Banking", {"a": 1})
    db.update_template_config("t1", {"a": 2})
    assert json.loads(db.get_template("t1")["config_json"]) == {"a": 2}


def test_get_template_missing_returns_none(db):
    assert db.get_template("no-such-id") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_templates.py -v`
Expected: FAIL — `AttributeError: 'Database' object has no attribute 'insert_template'`

- [ ] **Step 3: Add the table to `_SCHEMA`**

In `data/db.py`, add to the `_SCHEMA` triple-quoted string, after the `custody_events` table (or after `documents` if the custody tables aren't present in your checkout — place it as its own independent table, order relative to `bundles`/`custody_events` doesn't matter, it has no foreign keys):

```sql
CREATE TABLE IF NOT EXISTS project_templates (
    template_id  TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    category     TEXT NOT NULL,
    config_json  TEXT NOT NULL,
    built_in     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);
```

- [ ] **Step 4: Add the CRUD methods**

In `data/db.py`, add a new section after the custody section (or after `# -- documents` if no custody section exists in your checkout):

```python
    # -- project templates (Enhancement Plan §4.4) -------------------------
    def insert_template(self, template_id: str, name: str, category: str,
                        config: dict, built_in: bool = False) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO project_templates (template_id, name, "
                "category, config_json, built_in, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (template_id, name, category, json.dumps(config),
                 int(built_in), _now()))

    def get_template(self, template_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM project_templates WHERE template_id=?",
                (template_id,)).fetchone()

    def list_templates(self) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM project_templates "
                "ORDER BY category, name").fetchall()

    def update_template_config(self, template_id: str, config: dict) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE project_templates SET config_json=? "
                "WHERE template_id=?", (json.dumps(config), template_id))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_templates.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add data/db.py tests/test_templates.py
git commit -m "feat(templates): project_templates table with CRUD"
```

---

### Task 2: `TemplateManager` domain logic

**Files:**
- Create: `core/template_manager.py`
- Test: `tests/test_templates.py` (append)

**Interfaces:**
- Consumes: `Database.insert_template`, `Database.get_template`, `Database.list_templates`, `Database.update_template_config` (Task 1); `core.models.new_id() -> str`; `core.profile_manager.DEFAULT_PROJECT: dict`.
- Produces: `TemplateManager(db)`, `.list_templates() -> list[dict]`, `.get(template_id) -> dict`, `.save_as_template(project_config, name, category) -> dict`, `.build_project_config(template_id) -> dict`, `.seed_built_ins(templates_dir=None) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_templates.py`:

```python
from core.template_manager import TemplateManager


def test_save_as_template_strips_live_only_fields(db):
    project_config = {
        "project_id": "p1", "name": "Real Project", "version": 1,
        "status": "active", "created": "2026-08-29", "cloned_from": None,
        "scanner": {"dpi": 300}, "export": {"output_path": "/tmp/out"},
        "delivery": {"local_folder": {"path": "/tmp/deliver"}},
        "ocr": {"zones": [{"name": "invoice_no", "regex": r"(INV-\d+)"}]},
        "separation": {"strategy": ["blank_page"]},
        "qc": {"enabled": True, "stages": []},
        "enhancement_profile": {"deskew": True},
        "features": {"auto_index": True},
        "rules": {"rule_set_id": "r1", "version": 1, "rules": []},
    }
    tm = TemplateManager(db)
    saved = tm.save_as_template(project_config, "My Bank Template", "Banking")
    assert saved["name"] == "My Bank Template"
    assert saved["category"] == "Banking"
    assert saved["built_in"] is False
    cfg = saved["config"]
    for live_only in ("project_id", "name", "version", "status", "created",
                      "cloned_from", "scanner", "export", "delivery"):
        assert live_only not in cfg
    assert cfg["ocr"]["zones"][0]["name"] == "invoice_no"


def test_build_project_config_merges_onto_default(db):
    tm = TemplateManager(db)
    template = tm.save_as_template(
        {"ocr": {"zones": [{"name": "claim_no", "regex": r"(\S+)"}],
                "lang": "eng", "confidence_threshold": 0.75},
         "qc": {"enabled": True, "stages": ["margin"], "pass_score": 90,
               "blur_warn_threshold": 80, "blur_fail_threshold": 30}},
        "Insurance Starter", "Insurance")
    cfg = tm.build_project_config(template["template_id"])
    # schema-required keys absent from the template must come from DEFAULT_PROJECT
    assert "output_path" in cfg["export"]
    assert "local_folder" in cfg["delivery"]
    # template's own fields override the defaults
    assert cfg["ocr"]["zones"][0]["name"] == "claim_no"
    assert cfg["qc"]["pass_score"] == 90


def test_list_templates_returns_parsed_config(db):
    tm = TemplateManager(db)
    tm.save_as_template({"ocr": {"zones": []}}, "T", "Cat")
    templates = tm.list_templates()
    assert len(templates) == 1
    assert templates[0]["config"] == {"ocr": {"zones": []}}


def test_seed_built_ins_inserts_new_templates(db, tmp_path):
    templates_dir = tmp_path / "seed"
    templates_dir.mkdir()
    (templates_dir / "banking.json").write_text(json.dumps({
        "name": "Banking", "category": "Banking",
        "config": {"ocr": {"zones": []}},
    }), encoding="utf-8")
    tm = TemplateManager(db)
    tm.seed_built_ins(templates_dir)
    templates = tm.list_templates()
    assert len(templates) == 1
    assert templates[0]["name"] == "Banking"
    assert templates[0]["built_in"] is True


def test_seed_built_ins_updates_changed_built_in_in_place(db, tmp_path):
    templates_dir = tmp_path / "seed"
    templates_dir.mkdir()
    path = templates_dir / "banking.json"
    path.write_text(json.dumps({
        "name": "Banking", "category": "Banking",
        "config": {"ocr": {"zones": []}}}), encoding="utf-8")
    tm = TemplateManager(db)
    tm.seed_built_ins(templates_dir)
    first_id = tm.list_templates()[0]["template_id"]

    path.write_text(json.dumps({
        "name": "Banking", "category": "Banking",
        "config": {"ocr": {"zones": [{"name": "new_field"}]}}}),
        encoding="utf-8")
    tm.seed_built_ins(templates_dir)
    templates = tm.list_templates()
    assert len(templates) == 1  # updated in place, not duplicated
    assert templates[0]["template_id"] == first_id
    assert templates[0]["config"]["ocr"]["zones"][0]["name"] == "new_field"


def test_seed_built_ins_never_touches_admin_saved_template_of_same_name(db, tmp_path):
    tm = TemplateManager(db)
    tm.save_as_template({"ocr": {"zones": [{"name": "custom"}]}},
                        "Banking", "Banking")  # admin-saved, same name
    templates_dir = tmp_path / "seed"
    templates_dir.mkdir()
    (templates_dir / "banking.json").write_text(json.dumps({
        "name": "Banking", "category": "Banking",
        "config": {"ocr": {"zones": []}}}), encoding="utf-8")
    tm.seed_built_ins(templates_dir)
    templates = tm.list_templates()
    assert len(templates) == 2  # admin's + the newly-seeded built-in
    admin_one = next(t for t in templates if not t["built_in"])
    assert admin_one["config"]["ocr"]["zones"][0]["name"] == "custom"


def test_seed_built_ins_skips_malformed_json_without_raising(db, tmp_path, caplog):
    templates_dir = tmp_path / "seed"
    templates_dir.mkdir()
    (templates_dir / "broken.json").write_text("{not valid json", encoding="utf-8")
    tm = TemplateManager(db)
    tm.seed_built_ins(templates_dir)  # must not raise
    assert tm.list_templates() == []


def test_seed_built_ins_missing_directory_is_a_noop(db, tmp_path):
    tm = TemplateManager(db)
    tm.seed_built_ins(tmp_path / "does-not-exist")  # must not raise
    assert tm.list_templates() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_templates.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.template_manager'`

- [ ] **Step 3: Implement `core/template_manager.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_templates.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add core/template_manager.py tests/test_templates.py
git commit -m "feat(templates): TemplateManager - save/build/seed"
```

---

### Task 3: `extract_fields` skips unpositioned zones

**Files:**
- Modify: `ocr/zone_extractor.py`
- Test: find the existing test file for `ocr/zone_extractor.py` (run `grep -rl "extract_fields\|zone_extractor" tests/*.py` and use whichever file already imports/tests it — likely `tests/test_ocr.py`) and append to that file.

**Interfaces:**
- Consumes: nothing new.
- Produces: no interface change — `extract_fields(img, zones, lang="eng", ocr_fn=None) -> dict[str, FieldValue]` keeps its exact existing signature, just tolerates zones missing geometry.

**Why this task exists:** a project created from a template starts with zones that have `name`/`regex` but no `x`/`y`/`w`/`h` (position is sample-specific and set by the admin later). If `auto_index` is on and a scan happens before the admin positions every zone, today's `scaled_rect()` would raise `KeyError` on the missing `"x"` key, and that propagates out of the OCR worker. This task makes an unpositioned zone a silent no-field-extracted skip instead of a crash.

- [ ] **Step 1: Find the existing test file**

Run: `grep -rl "extract_fields\|from ocr.zone_extractor" tests/*.py`

Use the file that returns (most likely `tests/test_ocr.py`) for this task's test instead of creating a new one.

- [ ] **Step 2: Write the failing test**

Append to that file:

```python
def test_extract_fields_skips_zone_missing_geometry():
    """A template-seeded zone (name+regex, no x/y/w/h yet) must be
    silently skipped, not crash extraction."""
    from ocr.zone_extractor import extract_fields
    import numpy as np
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    zones = [{"name": "unpositioned", "regex": r"(\S+)"},
             {"name": "positioned", "x": 0, "y": 0, "w": 50, "h": 50}]

    def fake_ocr(region):
        from ocr.tesseract_engine import OCRResult
        return OCRResult(text="VALUE", confidence=0.9)

    fields = extract_fields(img, zones, ocr_fn=fake_ocr)
    assert "unpositioned" not in fields
    assert fields["positioned"].value == "VALUE"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ocr.py::test_extract_fields_skips_zone_missing_geometry -v`
Expected: FAIL — `KeyError: 'x'`

- [ ] **Step 4: Implement the fix**

In `ocr/zone_extractor.py`, inside `extract_fields`'s `for zone in zones:` loop, add a guard as the very first line of the loop body:

```python
    for zone in zones:
        if not all(k in zone for k in ("x", "y", "w", "h")):
            continue  # not yet positioned (e.g. a fresh template zone)
        zx, zy, zw, zh = scaled_rect(zone, w, h)
```

(This replaces just the `zx, zy, zw, zh = scaled_rect(zone, w, h)` line's context — insert the `if`/`continue` immediately before it, keep everything else in the function unchanged.)

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ocr.py::test_extract_fields_skips_zone_missing_geometry -v`
Expected: PASS

- [ ] **Step 6: Run the full suite to check for regressions**

Run: `QT_QPA_PLATFORM=offscreen .venv\Scripts\python.exe -m pytest -q`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add ocr/zone_extractor.py tests/
git commit -m "fix(ocr): extract_fields skips a zone with no geometry yet instead of crashing"
```

---

### Task 4: Built-in template JSON files

**Files:**
- Create: `core/templates/banking.json`
- Create: `core/templates/insurance.json`
- Create: `core/templates/hr.json`
- Create: `core/templates/legal.json`
- Create: `core/templates/invoices.json`
- Test: `tests/test_templates.py` (append)

**Interfaces:**
- Consumes: `TemplateManager.seed_built_ins` (Task 2) reads these files.
- Produces: five files matching the `{"name": ..., "category": ..., "config": {...}}` shape `seed_built_ins` expects.

Each file uses the same reusable-settings shape: `ocr` (zones with `name`+`regex` only, `lang`, `confidence_threshold`), `separation`, `qc`, `enhancement_profile`, `features`. `":\s*(\S+)"` is used for single-token fields (codes, numbers, dates), `":\s*(.+)"` for name-like fields that may contain spaces — the admin refines both position and regex against their own sample pages after creating the project, same as any hand-built zone (per the "Suggest regex" workflow already in `ui/zone_dialog.py`).

- [ ] **Step 1: Create `core/templates/banking.json`**

```json
{
  "name": "Banking",
  "category": "Banking",
  "config": {
    "ocr": {
      "lang": "eng",
      "confidence_threshold": 0.75,
      "zones": [
        {"name": "document_type", "regex": ":\\s*(.+)"},
        {"name": "customer_account_reference", "regex": ":\\s*(\\S+)"},
        {"name": "branch_code", "regex": ":\\s*(\\S+)"},
        {"name": "document_date", "regex": ":\\s*(\\S+)"},
        {"name": "reference_number", "regex": ":\\s*(\\S+)"}
      ]
    },
    "separation": {"strategy": ["blank_page"], "blank_threshold": 0.02},
    "qc": {"enabled": true, "stages": ["punch_hole", "rotation", "margin",
          "color", "advanced"], "pass_score": 85, "blur_warn_threshold": 80,
          "blur_fail_threshold": 30, "dpi_warn": 200, "dpi_fail": 150},
    "enhancement_profile": {"deskew": true, "crop": true, "clahe": true,
                            "despeckle": true, "whiten_bg": true,
                            "target_dpi": 300},
    "features": {"auto_index": true, "duplicate_check": true}
  }
}
```

- [ ] **Step 2: Create `core/templates/insurance.json`**

```json
{
  "name": "Insurance",
  "category": "Insurance",
  "config": {
    "ocr": {
      "lang": "eng",
      "confidence_threshold": 0.75,
      "zones": [
        {"name": "claim_number", "regex": ":\\s*(\\S+)"},
        {"name": "policy_number", "regex": ":\\s*(\\S+)"},
        {"name": "claimant_name", "regex": ":\\s*(.+)"},
        {"name": "date_of_loss", "regex": ":\\s*(\\S+)"},
        {"name": "adjuster_reference", "regex": ":\\s*(\\S+)"}
      ]
    },
    "separation": {"strategy": ["blank_page"], "blank_threshold": 0.02},
    "qc": {"enabled": true, "stages": ["punch_hole", "rotation", "margin",
          "color", "advanced"], "pass_score": 80, "blur_warn_threshold": 80,
          "blur_fail_threshold": 30, "dpi_warn": 200, "dpi_fail": 150},
    "enhancement_profile": {"deskew": true, "crop": true, "clahe": true,
                            "despeckle": true, "whiten_bg": true,
                            "target_dpi": 300},
    "features": {"auto_index": true, "duplicate_check": true}
  }
}
```

- [ ] **Step 3: Create `core/templates/hr.json`**

```json
{
  "name": "HR / Personnel",
  "category": "HR/Personnel",
  "config": {
    "ocr": {
      "lang": "eng",
      "confidence_threshold": 0.75,
      "zones": [
        {"name": "employee_id", "regex": ":\\s*(\\S+)"},
        {"name": "employee_name", "regex": ":\\s*(.+)"},
        {"name": "department", "regex": ":\\s*(.+)"},
        {"name": "document_type", "regex": ":\\s*(.+)"},
        {"name": "effective_date", "regex": ":\\s*(\\S+)"}
      ]
    },
    "separation": {"strategy": ["blank_page"], "blank_threshold": 0.02},
    "qc": {"enabled": true, "stages": ["punch_hole", "rotation", "margin",
          "color", "advanced"], "pass_score": 80, "blur_warn_threshold": 80,
          "blur_fail_threshold": 30, "dpi_warn": 200, "dpi_fail": 150},
    "enhancement_profile": {"deskew": true, "crop": true, "clahe": true,
                            "despeckle": true, "whiten_bg": true,
                            "target_dpi": 300},
    "features": {"auto_index": true, "duplicate_check": true}
  }
}
```

- [ ] **Step 4: Create `core/templates/legal.json`**

```json
{
  "name": "Legal / Contracts",
  "category": "Legal/Contracts",
  "config": {
    "ocr": {
      "lang": "eng",
      "confidence_threshold": 0.75,
      "zones": [
        {"name": "contract_number", "regex": ":\\s*(\\S+)"},
        {"name": "party_names", "regex": ":\\s*(.+)"},
        {"name": "effective_date", "regex": ":\\s*(\\S+)"},
        {"name": "contract_type", "regex": ":\\s*(.+)"},
        {"name": "reference_matter_number", "regex": ":\\s*(\\S+)"}
      ]
    },
    "separation": {"strategy": ["blank_page"], "blank_threshold": 0.02},
    "qc": {"enabled": true, "stages": ["punch_hole", "rotation", "margin",
          "color", "advanced"], "pass_score": 85, "blur_warn_threshold": 80,
          "blur_fail_threshold": 30, "dpi_warn": 200, "dpi_fail": 150},
    "enhancement_profile": {"deskew": true, "crop": true, "clahe": true,
                            "despeckle": true, "whiten_bg": true,
                            "target_dpi": 300},
    "features": {"auto_index": true, "duplicate_check": true}
  }
}
```

- [ ] **Step 5: Create `core/templates/invoices.json`**

```json
{
  "name": "Invoices / Receipts",
  "category": "Invoices",
  "config": {
    "ocr": {
      "lang": "eng",
      "confidence_threshold": 0.75,
      "zones": [
        {"name": "invoice_number", "regex": ":\\s*(\\S+)"},
        {"name": "vendor_name", "regex": ":\\s*(.+)"},
        {"name": "invoice_date", "regex": ":\\s*(\\S+)"},
        {"name": "amount", "regex": ":\\s*(\\S+)"},
        {"name": "po_reference", "regex": ":\\s*(\\S+)"}
      ]
    },
    "separation": {"strategy": ["blank_page"], "blank_threshold": 0.02},
    "qc": {"enabled": true, "stages": ["punch_hole", "rotation", "margin",
          "color", "advanced"], "pass_score": 80, "blur_warn_threshold": 80,
          "blur_fail_threshold": 30, "dpi_warn": 200, "dpi_fail": 150},
    "enhancement_profile": {"deskew": true, "crop": true, "clahe": true,
                            "despeckle": true, "whiten_bg": true,
                            "target_dpi": 300},
    "features": {"auto_index": true, "duplicate_check": true}
  }
}
```

- [ ] **Step 6: Write and run a test confirming all five load and seed correctly**

Append to `tests/test_templates.py`:

```python
def test_all_five_built_in_templates_load_and_seed(db):
    """Guards against a JSON typo in any of the five shipped files -
    exercises the real core/templates/ directory, not a synthetic one."""
    tm = TemplateManager(db)
    tm.seed_built_ins()  # default path: core/templates/ next to this module
    templates = tm.list_templates()
    names = {t["name"] for t in templates}
    assert names == {"Banking", "Insurance", "HR / Personnel",
                     "Legal / Contracts", "Invoices / Receipts"}
    for t in templates:
        assert t["built_in"] is True
        assert len(t["config"]["ocr"]["zones"]) == 5
        for zone in t["config"]["ocr"]["zones"]:
            assert "x" not in zone and "y" not in zone  # unpositioned
            assert zone["name"] and zone["regex"]


def test_every_built_in_template_config_produces_a_valid_project(db):
    """Each built-in must actually be usable: merged onto DEFAULT_PROJECT
    and run through the real schema validator, the same check
    ProfileManager.create() performs."""
    from core.profile_manager import ProfileManager
    tm = TemplateManager(db)
    tm.seed_built_ins()
    for t in tm.list_templates():
        cfg = tm.build_project_config(t["template_id"])
        cfg["project_id"] = "test"
        cfg["name"] = "Test"
        cfg["created"] = "2026-08-29"
        ProfileManager.validate(cfg)  # raises ProjectConfigError if invalid
```

Run: `.venv\Scripts\python.exe -m pytest tests/test_templates.py -v`
Expected: PASS (14 tests total)

- [ ] **Step 7: Commit**

```bash
git add core/templates/ tests/test_templates.py
git commit -m "feat(templates): five built-in starter templates (banking, insurance, hr, legal, invoices)"
```

---

### Task 5: Wire `seed_built_ins` into app startup

**Files:**
- Modify: `main.py`

**Interfaces:**
- Consumes: `TemplateManager(db).seed_built_ins()` (Task 2).

- [ ] **Step 1: Add the import and the call**

In `main.py`, add to the existing imports block:

```python
from core.template_manager import TemplateManager
```

In `main()`, immediately after the existing line `pm = ProfileManager(root / "projects", db)`, add:

```python
    TemplateManager(db).seed_built_ins()
```

(So the two lines read: `pm = ProfileManager(root / "projects", db)` then `TemplateManager(db).seed_built_ins()`, before `backup_service = BackupService(...)`.)

- [ ] **Step 2: Verify no import errors**

Run: `.venv\Scripts\python.exe -c "import main"`
Expected: no output, exit code 0

- [ ] **Step 3: Run the full suite**

Run: `QT_QPA_PLATFORM=offscreen .venv\Scripts\python.exe -m pytest -q`
Expected: all pass, no regressions

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat(templates): seed built-in templates on app startup"
```

---

### Task 6: "New from template…" in the Workflow Designer

**Files:**
- Modify: `ui/workflow_designer.py`
- Test: `tests/test_phase2_ui.py` (append)

**Interfaces:**
- Consumes: `TemplateManager.list_templates()`, `TemplateManager.build_project_config()` (Task 2); `ProfileManager.create(name, config=...)` (existing, unchanged).
- Produces: `WorkflowDesigner.new_from_template_btn: QPushButton`, `WorkflowDesigner.new_from_template() -> None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_phase2_ui.py`:

```python
def test_new_from_template_creates_project(designer, monkeypatch):
    from core.template_manager import TemplateManager
    tm = TemplateManager(designer.db)
    template = tm.save_as_template(
        {"ocr": {"zones": [{"name": "invoice_no", "regex": r"(INV-\d+)"}],
                "lang": "eng", "confidence_threshold": 0.75}},
        "Invoice Starter", "Invoices")

    monkeypatch.setattr(
        "ui.workflow_designer.QInputDialog.getItem",
        staticmethod(lambda *a, **k: (
            f"{template['category']} — {template['name']}", True)))
    monkeypatch.setattr(
        "ui.workflow_designer.QInputDialog.getText",
        staticmethod(lambda *a, **k: ("New Client Project", True)))

    before = {p["project_id"] for p in designer.db.list_projects()}
    designer.new_from_template()
    after = {p["project_id"] for p in designer.db.list_projects()}
    new_ids = after - before
    assert len(new_ids) == 1
    new_project = designer.pm.load(new_ids.pop())
    assert new_project["name"] == "New Client Project"
    assert new_project["ocr"]["zones"][0]["name"] == "invoice_no"
    # schema-required keys present even though the template didn't carry them
    assert "output_path" in new_project["export"]
    # the designer switches to showing the newly created project
    assert designer.project_combo.currentData() in new_ids or \
        designer.config["project_id"] == new_project["project_id"]


def test_new_from_template_no_templates_shows_message(designer, monkeypatch):
    shown = []
    monkeypatch.setattr(
        "ui.workflow_designer.QMessageBox.information",
        staticmethod(lambda *a, **k: shown.append(True)))
    designer.new_from_template()  # no templates saved in this fixture's db
    assert shown
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase2_ui.py -k new_from_template -v`
Expected: FAIL — `AttributeError: 'WorkflowDesigner' object has no attribute 'new_from_template'`

- [ ] **Step 3: Add the button**

In `ui/workflow_designer.py`, in `WorkflowDesigner.__init__`, immediately after the existing `self.clone_btn = QPushButton("Clone…")` / `self.clone_btn.clicked.connect(self.clone_project)` lines:

```python
        self.new_from_template_btn = QPushButton("New from template…")
        self.new_from_template_btn.clicked.connect(self.new_from_template)
```

Then in the existing loop:

```python
        for b in (self.clone_btn, self.rollback_btn, self.save_btn):
            b.setEnabled(self.can_edit)
            top.addWidget(b)
```

change it to also include the new button:

```python
        for b in (self.new_from_template_btn, self.clone_btn,
                  self.rollback_btn, self.save_btn):
            b.setEnabled(self.can_edit)
            top.addWidget(b)
```

- [ ] **Step 4: Implement `new_from_template`**

Add near `clone_project` (after it, in the same "project selection" section of the class):

```python
    def new_from_template(self) -> None:
        from core.template_manager import TemplateManager
        tm = TemplateManager(self.db)
        templates = tm.list_templates()
        if not templates:
            QMessageBox.information(self, "No templates",
                                    "No project templates available yet. "
                                    "Save a configured project as a "
                                    "template first, or check that "
                                    "built-in templates seeded correctly.")
            return
        labels = [f"{t['category']} — {t['name']}" for t in templates]
        label, ok = QInputDialog.getItem(
            self, "New from template", "Template:", labels, 0, False)
        if not ok:
            return
        template = templates[labels.index(label)]
        name, ok = QInputDialog.getText(self, "New project",
                                        "New project name:")
        if not (ok and name.strip()):
            return
        cfg = tm.build_project_config(template["template_id"])
        new_cfg = self.pm.create(name.strip(), config=cfg)
        self.refresh_projects()
        idx = self.project_combo.findData(new_cfg["project_id"])
        if idx >= 0:
            self.project_combo.setCurrentIndex(idx)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen .venv\Scripts\python.exe -m pytest tests/test_phase2_ui.py -k new_from_template -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add ui/workflow_designer.py tests/test_phase2_ui.py
git commit -m "feat(templates): 'New from template' button in the Workflow Designer"
```

---

### Task 7: "Save as template…" in the Workflow Designer

**Files:**
- Modify: `ui/workflow_designer.py`
- Test: `tests/test_phase2_ui.py` (append)

**Interfaces:**
- Consumes: `TemplateManager.save_as_template()` (Task 2); `WorkflowDesigner.collect_config()` (existing method, already used by `save()` — confirm its exact name by reading the file if it differs).
- Produces: `WorkflowDesigner.save_template_btn: QPushButton`, `WorkflowDesigner.save_as_template() -> None`.

- [ ] **Step 1: Confirm `collect_config`'s exact name**

Read `ui/workflow_designer.py`'s `save()` method (already shown in this plan's File Structure exploration) — it calls `self.collect_config()` to build the config dict from the current widget state before saving. Use that exact method name in Step 3 below.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_phase2_ui.py`:

```python
def test_save_as_template_captures_current_config(designer, monkeypatch):
    designer.dpi.setValue(600)  # scanner.dpi must NOT appear in the template
    calls = iter([("My Template", True), ("Custom Category", True)])
    monkeypatch.setattr(
        "ui.workflow_designer.QInputDialog.getText",
        staticmethod(lambda *a, **k: next(calls)))
    monkeypatch.setattr(
        "ui.workflow_designer.QMessageBox.information",
        staticmethod(lambda *a, **k: None))

    designer.save_as_template()

    from core.template_manager import TemplateManager
    templates = TemplateManager(designer.db).list_templates()
    assert len(templates) == 1
    assert templates[0]["name"] == "My Template"
    assert templates[0]["category"] == "Custom Category"
    assert "scanner" not in templates[0]["config"]
    assert "delivery" not in templates[0]["config"]


def test_save_as_template_noop_with_no_project_loaded(qtbot, db, tmp_path):
    from core.profile_manager import ProfileManager
    from ui.workflow_designer import WorkflowDesigner
    pm = ProfileManager(tmp_path / "projects3", db)
    d = WorkflowDesigner(db, pm, ADMIN)
    qtbot.addWidget(d)
    d.config = None
    d.save_as_template()  # must not raise
    from core.template_manager import TemplateManager
    assert TemplateManager(db).list_templates() == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase2_ui.py -k save_as_template -v`
Expected: FAIL — `AttributeError: 'WorkflowDesigner' object has no attribute 'save_as_template'`

- [ ] **Step 4: Add the button**

In `ui/workflow_designer.py`, alongside `new_from_template_btn` from Task 6:

```python
        self.save_template_btn = QPushButton("Save as template…")
        self.save_template_btn.clicked.connect(self.save_as_template)
```

And extend the same enable/add loop from Task 6 once more:

```python
        for b in (self.new_from_template_btn, self.clone_btn,
                  self.rollback_btn, self.save_btn, self.save_template_btn):
            b.setEnabled(self.can_edit)
            top.addWidget(b)
```

- [ ] **Step 5: Implement `save_as_template`**

Add near `new_from_template`:

```python
    def save_as_template(self) -> None:
        if self.config is None:
            return
        name, ok = QInputDialog.getText(self, "Save as template",
                                        "Template name:")
        if not (ok and name.strip()):
            return
        category, ok = QInputDialog.getText(self, "Save as template",
                                            "Category:")
        if not (ok and category.strip()):
            return
        from core.template_manager import TemplateManager
        cfg = self.collect_config()
        TemplateManager(self.db).save_as_template(
            cfg, name.strip(), category.strip())
        QMessageBox.information(self, "Saved",
                                f"Saved as template “{name.strip()}”.")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen .venv\Scripts\python.exe -m pytest tests/test_phase2_ui.py -k save_as_template -v`
Expected: PASS (2 tests)

- [ ] **Step 7: Run the full suite**

Run: `QT_QPA_PLATFORM=offscreen .venv\Scripts\python.exe -m pytest -q`
Expected: all pass, no regressions

- [ ] **Step 8: Commit**

```bash
git add ui/workflow_designer.py tests/test_phase2_ui.py
git commit -m "feat(templates): 'Save as template' button in the Workflow Designer"
```

---

### Task 8: Packaging and docs

**Files:**
- Modify: `docscanner.spec`
- Modify: `README.md`

**Interfaces:**
- Consumes: nothing new — this task only touches build/doc metadata.

- [ ] **Step 1: Bundle `core/templates/` for the packaged EXE**

In `docscanner.spec`, find the existing `datas` list (which already has the `core/project_schema.json` and `separation/rule_schema.json` entries from an earlier packaging fix) and add:

```python
    ("core/templates", "core/templates"),
```

as another entry in that same `datas` list.

- [ ] **Step 2: Confirm this is intentionally outside the automated packaging regression test**

Run: `.venv\Scripts\python.exe -m pytest tests/test_packaging_spec.py -v`
Expected: PASS — this test's regex only matches a single-file `Path(__file__).parent / "file.ext"` pattern, not a directory glob, so it has nothing to say about this new entry. That's expected (documented in the design spec) — this step is just confirming the spec file is still well-formed Python after the edit, not that the test covers the new entry.

- [ ] **Step 3: Update the README**

In `README.md`, add a bullet under `## Phase status` (after the most recent existing bullet):

```markdown
- **Project templates** — complete: five built-in starter templates
  (Banking, Insurance, HR/Personnel, Legal/Contracts, Invoices) plus
  "Save as template" so admins can turn any configured project into a
  reusable starting point for the next one
  (`docs/DOK_Scanner_Enhancement_Plan (1).docx` §4.4, generalized beyond
  banking-only per project direction).
```

- [ ] **Step 4: Run the full suite one final time**

Run: `QT_QPA_PLATFORM=offscreen .venv\Scripts\python.exe -m pytest -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add docscanner.spec README.md
git commit -m "docs: document project templates; bundle core/templates/ for packaging"
```

---

## Self-Review Notes

- **Spec coverage:** every section of `docs/superpowers/specs/2026-08-29-project-templates-design.md` maps to a task — data model (Task 1), `TemplateManager` (Task 2), the schema-required-keys merge (`build_project_config`, Task 2), built-ins + packaging location (Tasks 4, 8), startup seeding (Task 5), both UI entry points (Tasks 6, 7). The unpositioned-zone crash risk the design's own zone-content decision introduces is covered by Task 3, which the original spec flagged as a consequence to handle but hadn't yet turned into an explicit task — added here during planning.
- **Placeholder scan:** no TBD/TODO markers; every code block is complete, runnable code, not a description of code.
- **Type consistency check:** `TemplateManager.list_templates()`/`.get()` both return the same shape (`dict` with a parsed `config` key, `built_in` as a real `bool`) — confirmed Task 6/7's tests read `template["config"]`/`template["name"]`/`template["category"]`/`template["built_in"]`/`template["template_id"]` consistently with what `_to_dict` in Task 2 produces. `build_project_config`'s return value is fed straight to `ProfileManager.create(name, config=...)` in Task 6, matching `create()`'s existing `config: dict | None = None` parameter exactly (verified against `core/profile_manager.py:90` during design).
