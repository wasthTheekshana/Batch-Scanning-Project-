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
