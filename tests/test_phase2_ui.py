"""Phase 2 UI tests: admin panel, workflow designer, vendor sources."""
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QListWidgetItem

from core.profile_manager import ProfileManager
from core.rbac import Session, User, hash_password
from data.backup import BackupService
from data.db import Database
from scanning.alaris_source import AlarisSource
from scanning.base import ScannerUnavailable
from scanning.czur_source import CZURSource
from scanning.kodak_source import KodakSource
from scanning.sources import SOURCE_REGISTRY
from scanning.viisan_source import ViisanSource
from ui.admin_panel import AdminPanel, UsersTab
from ui.workflow_designer import RuleDialog, WorkflowDesigner

ADMIN = User("a1", "root", "admin")
VIEWER = User("v1", "view", "viewer")


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "p2.db")
    d.insert_user("u0", "admin", hash_password("admin"), "admin")
    yield d
    Session.logout()
    d.close()


# ---- admin panel ---------------------------------------------------------------

def test_users_tab_lists_and_gates(qtbot, db):
    tab = UsersTab(db, ADMIN)
    qtbot.addWidget(tab)
    assert tab.table.rowCount() == 1
    assert tab.add_btn.isEnabled()

    viewer_tab = UsersTab(db, VIEWER)
    qtbot.addWidget(viewer_tab)
    assert not viewer_tab.add_btn.isEnabled()


def test_admin_panel_backup_run_now(qtbot, db, tmp_path):
    svc = BackupService(db, [], tmp_path / "backups", b"k" * 32)
    panel = AdminPanel(db, ADMIN, backup_service=svc)
    qtbot.addWidget(panel)
    assert panel.backup_tab.run_btn.isEnabled()
    panel.backup_tab.run_now()
    assert "Backup OK" in panel.backup_tab.result.text()
    assert list((tmp_path / "backups").glob("*.dokbak"))


# ---- workflow designer -----------------------------------------------------------

@pytest.fixture()
def designer(qtbot, db, tmp_path):
    pm = ProfileManager(tmp_path / "projects", db)
    pm.create("Designer Project")
    d = WorkflowDesigner(db, pm, ADMIN)
    qtbot.addWidget(d)
    return d


def test_designer_loads_project(designer):
    assert designer.config is not None
    assert designer.qc_enabled.isChecked()
    assert designer.pass_score.value() == 80
    assert designer.dpi_warn.value() == 200
    assert designer.dpi_fail.value() == 150


def test_designer_ocr_mode_roundtrip(designer):
    assert designer.ocr_mode.currentText() == "Zone-based (draw regions)"
    designer.ocr_mode.setCurrentText("Full-page (label-anchored regex)")
    cfg = designer.collect_config()
    assert cfg["ocr"]["mode"] == "full_page"


def test_designer_dpi_thresholds_round_trip_to_saved_json(designer):
    designer.dpi_warn.setValue(250)
    designer.dpi_fail.setValue(180)
    cfg = designer.collect_config()
    saved = designer.pm.save(cfg)
    assert saved["qc"]["dpi_warn"] == 250
    assert saved["qc"]["dpi_fail"] == 180


def test_qc_strictness_preset_sets_all_thresholds(designer):
    designer.qc_preset.setCurrentText("Strict")
    assert designer.pass_score.value() == 90
    assert designer.blur_warn.value() == 100
    assert designer.blur_fail.value() == 50
    assert designer.dpi_warn.value() == 250
    assert designer.dpi_fail.value() == 200

    designer.qc_preset.setCurrentText("Lenient")
    assert designer.pass_score.value() == 65
    assert designer.blur_warn.value() == 60
    assert designer.blur_fail.value() == 15
    assert designer.dpi_warn.value() == 150
    assert designer.dpi_fail.value() == 100

    designer.qc_preset.setCurrentText("Standard")
    assert designer.pass_score.value() == 80
    assert designer.blur_warn.value() == 80
    assert designer.blur_fail.value() == 30
    assert designer.dpi_warn.value() == 200
    assert designer.dpi_fail.value() == 150


def test_qc_strictness_preset_starts_on_custom_for_nonstandard_project(qtbot, db, tmp_path):
    """A project whose thresholds don't match any named preset (e.g. one
    hand-tuned before this feature existed) must not silently get
    relabeled - the preset combo should reflect reality, not force a
    choice onto existing data."""
    from core.profile_manager import ProfileManager
    from ui.workflow_designer import WorkflowDesigner
    pm = ProfileManager(tmp_path / "projects_custom", db)
    cfg = pm.create("Custom QC Project")
    cfg["qc"]["pass_score"] = 77
    pm.save(cfg)
    d = WorkflowDesigner(db, pm, ADMIN)
    qtbot.addWidget(d)
    assert d.qc_preset.currentText() == "Custom"


def test_designer_edits_round_trip_to_saved_json(designer):
    designer.dpi.setValue(600)
    designer.qc_stage_checks["punch_hole"].setChecked(False)
    designer.feature_checks["duplicate_check"].setChecked(False)
    designer.strategy_checks["barcode"].setChecked(True)
    designer.filename_template.setText("{date}_{barcode}_{counter:04d}")

    cfg = designer.collect_config()
    saved = designer.pm.save(cfg)
    assert saved["version"] == 2
    assert saved["scanner"]["dpi"] == 600
    assert "punch_hole" not in saved["qc"]["stages"]
    assert saved["features"]["duplicate_check"] is False
    assert "barcode" in saved["separation"]["strategy"]


def test_designer_rule_builder_emits_valid_dsl(designer, qtbot):
    dlg = RuleDialog()
    qtbot.addWidget(dlg)
    dlg.name.setText("Flag missing invoice number")
    dlg._append_check({"field": "extracted_fields.invoice_no",
                       "op": "is_empty"})
    dlg._append_check({"field": "document_type", "op": "eq",
                       "value": "invoice"})
    dlg._append_action({"type": "add_flag",
                        "value": "missing_invoice_number"})
    rule = dlg.build_rule("r001")
    assert rule["condition"]["operator"] == "AND"
    assert len(rule["condition"]["checks"]) == 2

    item = QListWidgetItem("r001")
    item.setData(Qt.ItemDataRole.UserRole, rule)
    designer.rule_list.addItem(item)
    cfg = designer.collect_config()
    from separation.rule_engine import RuleEngine
    engine = RuleEngine(cfg["rules"])   # validates against schema
    doc = {"document_type": "invoice", "extracted_fields": {}}
    assert engine.evaluate(doc)
    assert doc["flags"] == ["missing_invoice_number"]


def test_designer_rollback_restores(designer):
    original_dpi = designer.config["scanner"]["dpi"]
    designer.dpi.setValue(600)
    designer.config = designer.pm.save(designer.collect_config())  # v2
    restored = designer.pm.rollback(designer.config["project_id"], 1)
    assert restored["scanner"]["dpi"] == original_dpi


def test_designer_read_only_for_non_admin(qtbot, db, tmp_path):
    pm = ProfileManager(tmp_path / "projects2", db)
    pm.create("P")
    d = WorkflowDesigner(db, pm, VIEWER)
    qtbot.addWidget(d)
    assert not d.save_btn.isEnabled()
    assert not d.qc_enabled.isEnabled()
    assert not d.add_rule_btn.isEnabled()


def test_designer_sftp_fields_round_trip(designer):
    designer.feature_checks["sftp"].setChecked(True)
    designer.sftp_host.setText("192.168.1.50")
    designer.sftp_user.setText("scans")
    designer.sftp_remote_path.setText("/incoming")
    cfg = designer.collect_config()
    assert cfg["delivery"]["sftp"]["enabled"] is True
    assert cfg["delivery"]["sftp"]["host"] == "192.168.1.50"
    assert cfg["delivery"]["sftp"]["username"] == "scans"
    assert cfg["delivery"]["sftp"]["remote_path"] == "/incoming"
    saved = designer.pm.save(cfg)   # passes schema validation
    assert saved["delivery"]["sftp"]["host"] == "192.168.1.50"


def test_new_from_template_zone_can_be_positioned_and_saved(designer, monkeypatch):
    """The exact end-to-end sequence Task 6's fix round was about: create a
    project from a template (zones have no geometry yet), confirm the
    designer's zone list renders them without crashing, open one in
    ZoneDialog (must not crash - this is what broke before the fix round),
    simulate the admin positioning it, and confirm the position survives
    a real save."""
    from core.template_manager import TemplateManager
    from PyQt6.QtCore import Qt
    from ui.zone_dialog import ZoneDialog

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
    designer.new_from_template()

    # zone list must render the unpositioned zone without crashing
    assert designer.zone_list.count() == 1
    item = designer.zone_list.item(0)
    zone = item.data(Qt.ItemDataRole.UserRole)
    assert zone["name"] == "invoice_no"
    assert "x" not in zone

    # opening Edit... on it must not crash - this is exactly what Task 6's
    # fix round fixed; confirm the fix actually holds through this real path
    dlg = ZoneDialog(zone)
    assert dlg.x.value() == 0  # defaulted, not crashed

    # simulate the admin dragging a region and saving
    dlg.name.setText("invoice_no")
    dlg.x.setValue(10); dlg.y.setValue(20)
    dlg.w.setValue(100); dlg.h.setValue(50)
    positioned = dlg.build_zone()
    assert positioned is not None
    item.setData(Qt.ItemDataRole.UserRole, positioned)

    cfg = designer.collect_config()
    saved = designer.pm.save(cfg)
    assert saved["ocr"]["zones"][0]["x"] == 10
    assert saved["ocr"]["zones"][0]["name"] == "invoice_no"


def test_admin_burn_tab(qtbot, db, tmp_path):
    import sys
    from delivery.cddvd_channel import CDDVDChannel
    script = tmp_path / "burner.py"
    script.write_text(
        "import shutil, sys, pathlib\n"
        "src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])\n"
        "dst.mkdir(parents=True, exist_ok=True)\n"
        "[shutil.copy2(f, dst / f.name) for f in src.iterdir()"
        " if f.is_file() and f.name != 'manifest.json']\n")
    disc = tmp_path / "disc"
    channel = CDDVDChannel({
        "enabled": True, "staging_dir": str(tmp_path / "queue"),
        "verify_dir": str(disc),
        "burner_cmd": f'"{sys.executable}" "{script}" '
                      f'"{{source_dir}}" "{disc}"'})
    doc = tmp_path / "doc.pdf"
    doc.write_bytes(b"%PDF-x")
    channel.deliver(str(doc), {})

    from ui.admin_panel import BurnTab
    tab = BurnTab(channel, ADMIN)
    qtbot.addWidget(tab)
    assert "1 file(s)" in tab.queue_label.text()
    tab.label_edit.setText("UAT_DISC-1")
    tab.burn_now()
    assert "Burn OK" in tab.result.text()
    assert (disc / "doc.pdf").exists()
    assert "0 file(s)" in tab.queue_label.text()


def test_admin_burn_tab_disabled_without_channel(qtbot, db):
    from ui.admin_panel import BurnTab
    tab = BurnTab(None, ADMIN)
    qtbot.addWidget(tab)
    assert not tab.burn_btn.isEnabled()


# ---- vendor SDK sources ------------------------------------------------------------

@pytest.mark.parametrize("cls", [KodakSource, AlarisSource,
                                 CZURSource, ViisanSource])
def test_vendor_sources_degrade_without_dll(cls):
    src = cls()
    assert src.list_devices() == []
    with pytest.raises(ScannerUnavailable):
        src.connect("Some Device")
    src.disconnect()


def test_source_registry_complete():
    assert set(SOURCE_REGISTRY) == {"twain", "wia", "kodak", "alaris",
                                    "czur", "viisan"}


# ---- new from template --------------------------------------------------------------

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


def test_zone_label_defaults_missing_geometry(designer):
    # a template-derived (or full-page-mode) zone may carry only
    # name/regex, without a captured pixel region - the label must not
    # raise, and should not show a meaningless (0, 0) 0x0 placeholder.
    label = designer._zone_label({"name": "invoice_no", "regex": r"(INV-\d+)"})
    assert "invoice_no" in label
    assert "full-page" in label
    assert "(0, 0)" not in label
    assert "0×0" not in label
    assert "/(INV-\\d+)/" in label


def test_zone_label_shows_real_geometry(designer):
    # a fully-geometried zone must display its real values, not the
    # geometry-less fallback.
    label = designer._zone_label(
        {"name": "invoice_no", "x": 12, "y": 34, "w": 56, "h": 78,
         "regex": r"(INV-\d+)"})
    assert "(12, 34)" in label
    assert "56×78" in label


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
