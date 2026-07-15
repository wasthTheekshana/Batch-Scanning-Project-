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
