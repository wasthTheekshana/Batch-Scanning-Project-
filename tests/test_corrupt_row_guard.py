"""The app survives a corrupt stored project / template config.

A projects (or project_templates) row whose config_json is malformed JSON, or
valid JSON that is not an object, must not crash startup, the Scan wizard, the
Index tab, central sync, the template list or the Workflow Designer. Nothing
that is logged or shown may contain the stored content (SENTINEL).
"""
import cv2
import json
import logging

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QInputDialog, QMessageBox

from sync.central_client import PullResult
from sync.central_state import CentralState
from sync.central_sync import CentralSyncService, choose_pipeline_profile
from ui.event_bus import EventBus
from pipeline.job_queue import Pipeline
from projects.profile_manager import ProfileManager
from pipeline.project_runtime import RuntimeResolver
from projects.project_store import load_config
from security.rbac import Session, User
from projects.template_manager import TemplateManager
from data.audit import AuditLogger
from data.db import Database
from delivery.local_folder import LocalFolderChannel
from export.batch_exporter import export_document
from main import build_default_project
from tests.conftest import make_text_page
from tests.test_central_sync import (FakeClient, PID, PID2, project_entry)
from tests.test_pipeline import wait_for
from tests.test_project_runtime import valid_profile
from ui import main_window as main_window_module
from ui.main_window import MainWindow
from ui.scan_wizard import ScanWizard
from ui.workflow_designer import UNREADABLE_BANNER, WorkflowDesigner

SENTINEL = "S3CRET-VALUE-123"
CORRUPT_VALUES = [
    '{"broken": "' + SENTINEL,              # malformed (truncated) JSON
    json.dumps([SENTINEL]),                 # valid JSON, not an object
    json.dumps(SENTINEL),                   # a JSON string
    "null",
    "",
]
READ_FAILED = "could not be read"


def corrupt_project(db, project_id, value=CORRUPT_VALUES[0]):
    db.conn.execute("UPDATE projects SET config_json=? WHERE project_id=?",
                    (value, project_id))
    db.conn.commit()


def add_project(pm, name, **over):
    cfg = pm.create(name)
    cfg.update(over)
    return cfg


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "corrupt.db")
    yield d
    d.close()


@pytest.fixture()
def pm(tmp_path, db):
    return ProfileManager(tmp_path / "projects", db)


class Shown(list):
    """(kind, title, text) per message box; .formats = text format per box
    built as an instance (those shown via exec())."""

    def __init__(self):
        super().__init__()
        self.formats = []


@pytest.fixture()
def messages(monkeypatch):
    shown = Shown()

    def record(kind):
        def fake(parent, title, text, *args, **kwargs):
            shown.append((kind, title, text))
            return QMessageBox.StandardButton.Ok
        return staticmethod(fake)

    for kind in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, kind, record(kind))

    def fake_exec(self):
        shown.append(("warning", self.windowTitle(), self.text()))
        shown.formats.append(self.textFormat())
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    return shown


# ---- the helper -----------------------------------------------------------
def test_load_config_returns_a_dict_for_a_good_row():
    assert load_config({"project_id": "p", "config_json": '{"a": 1}'}) == {"a": 1}


@pytest.mark.parametrize("value", CORRUPT_VALUES)
def test_load_config_returns_none_and_logs_the_id_only(value, caplog):
    caplog.set_level(logging.DEBUG)
    row = {"project_id": "proj-xyz", "config_json": value}
    assert load_config(row) is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "proj-xyz" in warnings[0].getMessage()
    assert SENTINEL not in caplog.text


def test_load_config_none_value_is_unreadable_and_a_template_logs_its_id(caplog):
    row = {"template_id": "tmpl-1", "config_json": None}
    assert load_config(row, kind="template") is None
    assert "tmpl-1" in caplog.text


def test_load_config_tolerates_a_row_without_an_id_column(caplog):
    assert load_config({"config_json": "{oops"}) is None
    assert len([r for r in caplog.records
                if r.levelno == logging.WARNING]) == 1


# ---- site 1: main.build_default_project -----------------------------------
def test_default_project_with_only_corrupt_projects_creates_the_first_run_project(
        tmp_path, db, pm, caplog):
    bad = add_project(pm, "Only bad")
    corrupt_project(db, bad["project_id"])
    cfg = build_default_project(pm, db, tmp_path)
    assert cfg["name"] == "General Documents"
    assert cfg["project_id"] != bad["project_id"]
    assert SENTINEL not in caplog.text


def test_default_project_prefers_the_readable_project_over_a_corrupt_first_one(
        tmp_path, db, pm, caplog):
    bad = add_project(pm, "Aaa bad")
    good = add_project(pm, "Zzz good")
    corrupt_project(db, bad["project_id"])
    cfg = build_default_project(pm, db, tmp_path)
    assert cfg["project_id"] == good["project_id"]
    assert len(db.list_projects()) == 2            # nothing new created
    assert SENTINEL not in caplog.text


# ---- site 2: choose_pipeline_profile --------------------------------------
def test_pipeline_profile_skips_an_unreadable_managed_project(db, pm, caplog):
    bad = add_project(pm, "Aaa bad")
    good = add_project(pm, "Zzz good")
    for p in (bad, good):
        db.set_central_project(p["project_id"], 1, managed=True)
    corrupt_project(db, bad["project_id"])
    chosen = choose_pipeline_profile(db, fallback=lambda: {"from": "fallback"})
    assert chosen["project_id"] == good["project_id"]
    assert SENTINEL not in caplog.text


def test_pipeline_profile_falls_back_when_every_managed_project_is_unreadable(
        db, pm):
    bad = add_project(pm, "Only bad")
    db.set_central_project(bad["project_id"], 1, managed=True)
    corrupt_project(db, bad["project_id"])
    assert choose_pipeline_profile(
        db, fallback=lambda: {"from": "fallback"}) == {"from": "fallback"}


# ---- central sync (sites 3 and 4) -----------------------------------------
@pytest.fixture()
def sync(tmp_path, db, pm):
    state = CentralState(tmp_path)
    state.register_result("https://c.example", "pc-1", "dsk_KEY", 600)
    audit = AuditLogger(db, tmp_path / "logs")
    holder = {}
    svc = CentralSyncService(
        db, pm, state, tmp_path, audit=audit,
        client_factory=lambda url, timeout: holder["client"])
    env = type("Env", (), dict(db=db, pm=pm, svc=svc, root=tmp_path,
                               holder=holder))
    yield env
    audit.close()


def pull(env, *entries):
    env.holder["client"] = FakeClient(pull=PullResult("pc-1", 600,
                                                      list(entries)))


def audit_actions(db):
    return [r["action"] for r in db.conn.execute(
        "SELECT action FROM audit_log ORDER BY log_id")]


def test_seed_skips_an_unreadable_local_project_and_uses_the_next_readable_one(
        sync, caplog):
    bad = add_project(sync.pm, "Aaa bad")
    good = valid_profile("seed-good", "Zzz good", sync.root / "g_out",
                         sync.root / "g_deliv")
    sync.pm.adopt(good)
    corrupt_project(sync.db, bad["project_id"])
    pull(sync, project_entry())
    result = sync.svc.sync_once()
    assert result.applied == [PID]
    assert sync.pm.load(PID)["delivery"]["local_folder"]["path"] == \
        str(sync.root / "g_deliv")                 # seeded from the readable one
    assert SENTINEL not in caplog.text


def test_seed_uses_defaults_when_every_local_project_is_unreadable(sync):
    bad = add_project(sync.pm, "Only bad")
    corrupt_project(sync.db, bad["project_id"])
    pull(sync, project_entry())
    assert sync.svc.sync_once().applied == [PID]


def test_central_entry_for_an_unreadable_local_project_is_skipped_untouched(
        sync, caplog):
    sync.pm.adopt(project_entry(pid=PID, name="Local copy")["config"])
    corrupt_project(sync.db, PID)
    corrupt_value = sync.db.get_project(PID)["config_json"]
    file_before = (sync.root / "projects" / f"{PID}.json").read_bytes()
    pull(sync, project_entry(pid=PID, version=2), project_entry(pid=PID2,
                                                                name="Good"))
    result = sync.svc.sync_once()
    assert result.status == "ok"
    assert result.errors == {
        PID: "local copy unreadable - repair or re-save the project, then "
             "sync again"}
    assert result.applied == [PID2]
    assert result.unassigned == []
    assert sync.db.get_project(PID)["config_json"] == corrupt_value
    assert (sync.root / "projects" / f"{PID}.json").read_bytes() == file_before
    assert sync.db.get_central_project(PID) is None     # never marked applied
    assert "central_project_skipped" in audit_actions(sync.db)
    assert SENTINEL not in caplog.text
    assert SENTINEL not in str(sync.db.conn.execute(
        "SELECT detail FROM audit_log").fetchall())


def test_unreadable_local_project_that_central_still_assigns_is_not_unassigned(
        sync):
    sync.pm.adopt(project_entry(pid=PID, name="Local copy")["config"])
    sync.db.set_central_project(PID, 1, managed=True)
    corrupt_project(sync.db, PID)
    pull(sync, project_entry(pid=PID, version=2))
    result = sync.svc.sync_once()
    assert PID in result.errors
    assert result.unassigned == []
    assert sync.db.get_central_project(PID)["managed"] == 1


# ---- sites 5-7: MainWindow --------------------------------------------------
@pytest.fixture()
def window(qtbot, tmp_path, db, pm):
    audit = AuditLogger(db, tmp_path / "logs")
    Session._current = User("a1", "root", "admin")

    def make(pipeline=None):
        win = MainWindow(db, pm, EventBus(), pipeline=pipeline, audit=audit,
                         staging_dir=tmp_path / "staging")
        qtbot.addWidget(win)
        return win

    yield make
    Session.logout()
    audit.close()


class FakeWizard:
    """Stands in for ScanWizard so open_scan_wizard does not block."""
    created = []

    def __init__(self, projects, staging_dir, parent=None):
        self.projects = projects
        FakeWizard.created.append(self)

    class _Signal:
        def connect(self, _fn):
            pass

    batch_ready = _Signal()

    def exec(self):
        return 0


@pytest.fixture()
def fake_wizard(monkeypatch):
    FakeWizard.created = []
    monkeypatch.setattr(main_window_module, "ScanWizard", FakeWizard)
    return FakeWizard


def zone_project(pm, name, zone_name, mode="zone"):
    cfg = pm.create(name)
    cfg["ocr"]["zones"] = [{"name": zone_name, "x": 0, "y": 0, "w": 10,
                            "h": 10, "regex": r"(\S+)"}]
    cfg["ocr"]["mode"] = mode
    return pm.save(cfg)


def test_main_window_builds_and_uses_the_first_readable_project_for_the_index_tab(
        window, db, pm, caplog):
    bad = add_project(pm, "Aaa bad")
    zone_project(pm, "Zzz good", "invoice_no", mode="full_page")
    corrupt_project(db, bad["project_id"])
    win = window()
    assert [z["name"] for z in win.index_editor.zones] == ["invoice_no"]
    assert win.index_editor.full_page is True
    assert SENTINEL not in caplog.text


def test_main_window_builds_when_every_project_is_unreadable(window, db, pm):
    bad = add_project(pm, "Only bad")
    corrupt_project(db, bad["project_id"], CORRUPT_VALUES[1])
    win = window()
    assert win.index_editor.zones == []


def test_active_zones_and_mode_skip_unreadable_projects(window, db, pm):
    bad = add_project(pm, "Aaa bad")
    zone_project(pm, "Zzz good", "claim_no", mode="full_page")
    win = window()
    corrupt_project(db, bad["project_id"])
    assert [z["name"] for z in win._active_zones()] == ["claim_no"]
    assert win._active_ocr_mode() == "full_page"


def test_wizard_lists_only_readable_projects_and_warns_once_about_the_rest(
        window, db, pm, fake_wizard, messages, caplog):
    bad1 = add_project(pm, "Aaa broken one")
    bad2 = add_project(pm, "Bbb broken two")
    good = add_project(pm, "Zzz good")
    corrupt_project(db, bad1["project_id"])
    corrupt_project(db, bad2["project_id"], CORRUPT_VALUES[1])
    win = window()
    win.open_scan_wizard()
    assert len(fake_wizard.created) == 1
    assert [p["project_id"] for p in fake_wizard.created[0].projects] == \
        [good["project_id"]]
    assert len(messages) == 1
    kind, title, text = messages[0]
    assert (kind, title) == ("warning", "Projects not listed")
    assert text == ("2 project(s) are unreadable and are not listed: "
                    "Aaa broken one, Bbb broken two. Open the Workflow "
                    "Designer to repair them.")
    assert messages.formats == [Qt.TextFormat.PlainText]
    assert SENTINEL not in text and SENTINEL not in caplog.text


def test_wizard_warning_names_at_most_ten_projects(window, db, pm, fake_wizard,
                                                   messages):
    for i in range(12):
        corrupt_project(db, add_project(pm, f"Bad {i:02d}")["project_id"])
    add_project(pm, "Zzz good")
    win = window()
    win.open_scan_wizard()
    text = messages[0][2]
    assert text.startswith("12 project(s) are unreadable")
    assert "Bad 09" in text and "Bad 10" not in text
    assert "and 2 more" in text


def test_wizard_with_no_unreadable_project_shows_no_warning(
        window, pm, fake_wizard, messages):
    add_project(pm, "Fine")
    window().open_scan_wizard()
    assert messages == []
    assert len(fake_wizard.created) == 1


def test_wizard_is_not_opened_when_no_project_is_readable(
        window, db, pm, fake_wizard, messages):
    bad = add_project(pm, "Only bad")
    corrupt_project(db, bad["project_id"])
    win = window()
    win.open_scan_wizard()
    assert fake_wizard.created == []
    assert len(messages) == 1
    assert messages[0][1] == "Projects not listed"
    assert "Only bad" in messages[0][2]


# ---- end to end: real window + real pipeline --------------------------------
def test_end_to_end_a_corrupt_project_does_not_stop_a_good_projects_batch(
        qtbot, tmp_path, db, pm, monkeypatch, messages, caplog):
    bad = add_project(pm, "Aaa corrupt")
    cfg = pm.adopt(valid_profile("proj-good", "Zzz good", tmp_path / "out",
                                 tmp_path / "deliv"))
    corrupt_project(db, bad["project_id"])
    audit = AuditLogger(db, tmp_path / "logs")
    pipeline = Pipeline(
        db, cfg, [LocalFolderChannel(cfg["delivery"]["local_folder"])],
        export_document, working_dir=tmp_path / "pipe-staging",
        resolver=RuntimeResolver(db))
    pipeline.start()
    Session._current = User("a1", "root", "admin")
    try:
        win = MainWindow(db, pm, EventBus(), pipeline=pipeline, audit=audit,
                         staging_dir=tmp_path / "staging")
        qtbot.addWidget(win)
        img = tmp_path / "page.png"
        cv2.imwrite(str(img), make_text_page())
        listed = []

        def fake_exec(self):
            listed.extend(self.project_page.combo.itemData(i)
                          for i in range(self.project_page.combo.count()))
            self.source_page.file_list.addItem(str(img))
            self.accept()
            return 1

        monkeypatch.setattr(ScanWizard, "exec", fake_exec)
        win.open_scan_wizard()
        assert listed == ["proj-good"]
        assert len(messages) == 1 and messages[0][1] == "Projects not listed"
        batch = db.conn.execute(
            "SELECT batch_id FROM batches").fetchone()["batch_id"]

        def delivered():
            docs = db.list_documents(batch)
            return len(docs) == 1 and \
                docs[0]["delivery_status"] == "delivered_local"

        assert wait_for(delivered)
        assert db.get_batch(batch)["project_id"] == "proj-good"
        assert list((tmp_path / "deliv").glob("*"))
        assert SENTINEL not in caplog.text
    finally:
        pipeline.shutdown()
        Session.logout()
        audit.close()


# ---- site 8: templates -------------------------------------------------------
def corrupt_template(db, template_id, value=CORRUPT_VALUES[0]):
    db.conn.execute(
        "UPDATE project_templates SET config_json=? WHERE template_id=?",
        (value, template_id))
    db.conn.commit()


def test_list_templates_skips_an_unreadable_row_and_keeps_the_rest(db, caplog):
    db.insert_template("t-bad", "Aaa bad", "Cat", {})
    db.insert_template("t-good", "Zzz good", "Cat", {"ocr": {"zones": []}})
    corrupt_template(db, "t-bad")
    templates = TemplateManager(db).list_templates()
    assert [t["template_id"] for t in templates] == ["t-good"]
    assert "t-bad" in caplog.text
    assert SENTINEL not in caplog.text


@pytest.mark.parametrize("value", CORRUPT_VALUES)
def test_get_template_returns_none_for_an_unreadable_row(db, value, caplog):
    db.insert_template("t-bad", "Bad", "Cat", {})
    corrupt_template(db, "t-bad", value)
    assert TemplateManager(db).get("t-bad") is None
    assert SENTINEL not in caplog.text


def test_seed_built_ins_repairs_an_unreadable_built_in_in_place(db, tmp_path):
    templates_dir = tmp_path / "seed"
    templates_dir.mkdir()
    shipped = {"name": "Banking", "category": "Banking",
               "config": {"ocr": {"zones": []}}}
    (templates_dir / "banking.json").write_text(json.dumps(shipped),
                                                encoding="utf-8")
    tm = TemplateManager(db)
    tm.seed_built_ins(templates_dir)
    template_id = tm.list_templates()[0]["template_id"]
    corrupt_template(db, template_id)
    tm.seed_built_ins(templates_dir)                    # must not raise
    templates = tm.list_templates()
    assert len(db.list_templates()) == 1                # repaired, not duplicated
    assert templates[0]["template_id"] == template_id
    assert templates[0]["config"] == shipped["config"]


def test_seed_built_ins_leaves_an_unreadable_admin_template_alone(db, tmp_path):
    db.insert_template("t-admin", "Banking", "Banking", {}, built_in=False)
    corrupt_template(db, "t-admin")
    templates_dir = tmp_path / "seed"
    templates_dir.mkdir()
    (templates_dir / "banking.json").write_text(json.dumps({
        "name": "Banking", "category": "Banking", "config": {}}),
        encoding="utf-8")
    TemplateManager(db).seed_built_ins(templates_dir)
    assert db.get_template("t-admin")["config_json"] == CORRUPT_VALUES[0]


# ---- site 9: Workflow Designer ----------------------------------------------
def corrupt_file(pm, project_id, value=CORRUPT_VALUES[0]):
    (pm.projects_dir / f"{project_id}.json").write_text(value,
                                                         encoding="utf-8")


@pytest.fixture()
def designer(qtbot, db, pm):
    user = User("a1", "root", "admin")

    def make():
        d = WorkflowDesigner(db, pm, user)
        qtbot.addWidget(d)
        return d

    return make


def select(designer, project_id):
    designer.project_combo.setCurrentIndex(
        designer.project_combo.findData(project_id))


def test_designer_shows_a_banner_for_a_project_whose_file_is_unreadable(
        designer, pm, caplog, messages):
    bad = add_project(pm, "Aaa bad")
    good = add_project(pm, "Zzz good")
    corrupt_file(pm, bad["project_id"])
    d = designer()                      # the bad project is selected first
    assert not d.unreadable_banner.isHidden()
    assert d.unreadable_banner.text() == UNREADABLE_BANNER
    assert not d.form_body.isEnabled()
    assert d.config is None
    assert not d.save_btn.isEnabled()
    select(d, good["project_id"])       # other projects stay selectable
    assert d.unreadable_banner.isHidden()
    assert d.form_body.isEnabled()
    assert d.config["project_id"] == good["project_id"]
    assert d.save_btn.isEnabled()
    assert SENTINEL not in caplog.text
    assert not [m for m in messages if SENTINEL in m[2]]


@pytest.mark.parametrize("value", [CORRUPT_VALUES[1], '{"name": 1}'])
def test_designer_banner_also_covers_a_file_that_is_not_a_valid_config(
        designer, pm, value):
    bad = add_project(pm, "Only bad")
    corrupt_file(pm, bad["project_id"], value)
    d = designer()
    assert not d.unreadable_banner.isHidden()
    assert d.unreadable_banner.text() == UNREADABLE_BANNER


def test_designer_banner_when_the_project_file_is_missing(designer, pm):
    bad = add_project(pm, "Only bad")
    (pm.projects_dir / f"{bad['project_id']}.json").unlink()
    d = designer()
    assert not d.unreadable_banner.isHidden()
    assert d.config is None


def test_designer_clone_of_a_project_whose_file_went_bad_does_not_raise(
        designer, pm, monkeypatch, messages):
    good = add_project(pm, "Only")
    d = designer()
    corrupt_file(pm, good["project_id"])            # corrupted after loading
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("Copy", True)))
    d.clone_project()
    assert len(pm.db.list_projects()) == 1
    assert [m[0] for m in messages] == ["warning"]
    assert READ_FAILED in messages[0][2]


def test_designer_rollback_of_a_project_whose_file_went_bad_does_not_raise(
        designer, pm, monkeypatch, messages):
    cfg = add_project(pm, "Only")
    pm.save(pm.load(cfg["project_id"]))             # archives v1
    d = designer()
    corrupt_file(pm, cfg["project_id"])
    monkeypatch.setattr(QInputDialog, "getItem",
                        staticmethod(lambda *a, **k: ("1", True)))
    d.rollback_dialog()
    assert [m[0] for m in messages] == ["warning"]
    assert READ_FAILED in messages[0][2]


def test_designer_managed_local_save_with_a_bad_file_does_not_raise(
        designer, db, pm, messages):
    cfg = add_project(pm, "Only")
    db.set_central_project(cfg["project_id"], 1, managed=True)
    d = designer()
    corrupt_file(pm, cfg["project_id"])
    d.save()
    assert [m[0] for m in messages] == ["warning"]
    assert READ_FAILED in messages[0][2]


def test_designer_new_from_template_with_an_unreadable_template_does_not_raise(
        designer, db, pm, monkeypatch, messages):
    add_project(pm, "Existing")
    db.insert_template("t-bad", "Broken", "Cat", {})
    db.insert_template("t-ok", "Fine", "Cat", {})
    corrupt_template(db, "t-bad")
    d = designer()
    seen = {}

    def get_item(parent, title, label, items, *args, **kwargs):
        seen["items"] = list(items)
        return items[0], True

    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(get_item))
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("From tmpl", True)))
    d.new_from_template()
    assert seen["items"] == ["Cat — Fine"]
    assert len(db.list_projects()) == 2


# ---- fix round 1 -------------------------------------------------------------
def schema_invalid_file(pm, project_id):
    """Valid JSON, schema-invalid, with the sentinel as the offending value."""
    cfg = json.loads((pm.projects_dir / f"{project_id}.json").read_text(
        encoding="utf-8"))
    cfg["version"] = SENTINEL
    corrupt_file(pm, project_id, json.dumps(cfg))


def test_designer_managed_save_with_a_schema_invalid_stored_file_shows_fixed_text(
        designer, db, pm, messages, caplog):
    cfg = add_project(pm, "Only")
    db.set_central_project(cfg["project_id"], 1, managed=True)
    d = designer()
    schema_invalid_file(pm, cfg["project_id"])
    d.save()
    assert [(m[0], m[1], m[2]) for m in messages] == [
        ("warning", "Project unreadable", UNREADABLE_BANNER + ".")]
    assert SENTINEL not in caplog.text


def test_designer_save_validation_error_names_the_field_never_the_value(
        designer, pm, messages, caplog, monkeypatch):
    add_project(pm, "Only")
    d = designer()
    real = d.collect_config()
    monkeypatch.setattr(d, "collect_config",
                        lambda: {**real, "version": SENTINEL})
    d.save()
    assert len(messages) == 1
    kind, title, text = messages[0]
    assert (kind, title) == ("critical", "Invalid configuration")
    assert text == ("The configuration is not valid at version. Correct that "
                    "field and save again.")
    assert SENTINEL not in caplog.text


def test_designer_save_when_the_file_went_bad_after_loading_keeps_state(
        designer, pm, messages, caplog):
    cfg = add_project(pm, "Only")
    d = designer()
    corrupt_file(pm, cfg["project_id"])
    before = (pm.projects_dir / f"{cfg['project_id']}.json").read_bytes()
    d.save()
    assert [(m[0], m[2]) for m in messages] == [
        ("warning", UNREADABLE_BANNER + ".")]
    assert (pm.projects_dir / f"{cfg['project_id']}.json").read_bytes() == before
    assert d.config["project_id"] == cfg["project_id"]     # form state untouched
    assert SENTINEL not in caplog.text


def test_designer_clears_the_previous_projects_values_under_the_banner(
        designer, pm):
    bad = add_project(pm, "Aaa bad")
    good = pm.create("Zzz good")
    good["export"]["output_path"] = "D:/previous/output"
    good["delivery"]["sftp"] = {"enabled": False, "password": "previous-pw"}
    good["ocr"]["zones"] = [{"name": "prev_zone", "x": 0, "y": 0, "w": 5,
                             "h": 5, "regex": "(a)"}]
    pm.save(good)
    d = designer()
    select(d, good["project_id"])
    assert d.output_path.text() == "D:/previous/output"
    assert d.zone_list.count() == 1
    corrupt_file(pm, bad["project_id"])
    select(d, bad["project_id"])
    assert d.output_path.text() == ""
    assert d.sftp_password.text() == ""
    assert d.zone_list.count() == 0
    assert d.rule_list.count() == 0


def test_sync_with_an_intact_row_but_corrupt_project_file_is_skipped_with_fixed_reason(
        sync, caplog):
    sync.pm.adopt(project_entry(pid=PID, name="Local copy")["config"])
    corrupt_file(sync.pm, PID)
    pull(sync, project_entry(pid=PID, version=2),
         project_entry(pid=PID2, name="Good"))
    result = sync.svc.sync_once()
    assert result.status == "ok"
    assert result.errors == {
        PID: "local project file unreadable - repair or re-save the project, "
             "then sync again"}
    assert result.applied == [PID2]
    assert result.unassigned == []
    assert SENTINEL not in str(result)
    assert SENTINEL not in str(sync.db.conn.execute(
        "SELECT detail FROM audit_log").fetchall())
    assert SENTINEL not in caplog.text


def test_sync_records_only_the_exception_type_for_an_unexpected_failure(
        sync, monkeypatch, caplog):
    def boom(config):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(sync.pm, "adopt", boom)
    pull(sync, project_entry(pid=PID))
    result = sync.svc.sync_once()
    assert result.errors == {PID: "RuntimeError"}
    assert SENTINEL not in str(sync.db.conn.execute(
        "SELECT detail FROM audit_log").fetchall())
    assert SENTINEL not in caplog.text


def test_sync_schema_invalid_overlay_reports_the_field_never_the_value(
        sync, monkeypatch, caplog):
    real_adopt = sync.pm.adopt

    def adopt_bad(config):
        return real_adopt({**config, "version": SENTINEL})

    monkeypatch.setattr(sync.pm, "adopt", adopt_bad)
    pull(sync, project_entry(pid=PID))
    result = sync.svc.sync_once()
    assert result.errors == {PID: "invalid project config at version"}
    assert SENTINEL not in str(sync.db.conn.execute(
        "SELECT detail FROM audit_log").fetchall())
    assert SENTINEL not in caplog.text
