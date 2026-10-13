"""Workflow Designer: barcode role/pattern and document types."""
import pytest
from PyQt6.QtWidgets import QDialog

from core.profile_manager import ProfileManager
from core.rbac import User
from data.db import Database
from ui.document_type_dialog import DocumentTypeDialog
from ui.workflow_designer import WorkflowDesigner

ADMIN = User("u1", "root", "admin")

NIC = {"name": "NIC", "regex": "national identity card",
       "zones": [{"name": "nic_number", "regex": r"ID No:\s*(\d{12})"}]}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db = Database(tmp_path / "d.db")
    pm = ProfileManager(tmp_path / "projects", db)
    monkeypatch.setattr("ui.workflow_designer.QMessageBox.information",
                        staticmethod(lambda *a, **k: None))
    yield type("E", (), dict(db=db, pm=pm))
    db.close()


def designer(qtbot, env, **sections):
    cfg = env.pm.create("P")
    for section, values in sections.items():
        cfg[section].update(values)
    cfg = env.pm.save(cfg)
    d = WorkflowDesigner(env.db, env.pm, ADMIN)
    qtbot.addWidget(d)
    assert d.config["project_id"] == cfg["project_id"]
    return d


def test_barcode_role_and_pattern_round_trip_through_save(qtbot, env):
    d = designer(qtbot, env, separation={"strategy": ["barcode"],
                                         "barcode_role": "cover_sheet",
                                         "barcode_pattern": r"^CIF-\d+$"})
    assert d.barcode_role.currentData() == "cover_sheet"
    assert d.barcode_pattern.text() == r"^CIF-\d+$"
    d.barcode_role.setCurrentIndex(d.barcode_role.findData("auto"))
    d.barcode_pattern.setText("")
    d.save()
    saved = env.pm.load(d.config["project_id"])
    assert saved["separation"]["barcode_role"] == "auto"
    assert "barcode_pattern" not in saved["separation"]


def test_a_project_without_a_role_defaults_to_the_first_page(qtbot, env):
    d = designer(qtbot, env)
    assert d.barcode_role.currentData() == "first_page"
    assert d.collect_config()["separation"]["barcode_role"] == "first_page"


def test_document_types_load_into_the_list_and_save_back(qtbot, env):
    d = designer(qtbot, env, ocr={"mode": "full_page", "document_types": [NIC]})
    assert d.type_list.count() == 1
    assert d.type_list.item(0).text() == (
        "NIC — recognised by /national identity card/ · 1 field")
    d.save()
    assert env.pm.load(d.config["project_id"])["ocr"]["document_types"] == [NIC]


def test_a_project_with_no_types_keeps_no_document_types_key(qtbot, env):
    d = designer(qtbot, env)
    assert "document_types" not in d.collect_config()["ocr"]


def test_add_edit_and_remove_a_document_type(qtbot, env, monkeypatch):
    d = designer(qtbot, env, ocr={"mode": "full_page"})
    created = {"name": "Application", "regex": "application form",
               "zones": [{"name": "application_number", "regex": r"(APP-\d+)"}]}
    monkeypatch.setattr(DocumentTypeDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(DocumentTypeDialog, "build_type", lambda self: created)
    d.add_type()
    assert d.collect_config()["ocr"]["document_types"] == [created]
    edited = dict(created, regex="loan application form")
    monkeypatch.setattr(DocumentTypeDialog, "build_type", lambda self: edited)
    d.type_list.setCurrentRow(0)
    d.edit_type()
    assert d.collect_config()["ocr"]["document_types"] == [edited]
    d.type_list.setCurrentRow(0)
    d.del_type_btn.click()
    assert "document_types" not in d.collect_config()["ocr"]


def test_cancelling_the_dialog_adds_nothing(qtbot, env, monkeypatch):
    d = designer(qtbot, env)
    monkeypatch.setattr(DocumentTypeDialog, "exec",
                        lambda self: QDialog.DialogCode.Rejected)
    d.add_type()
    assert d.type_list.count() == 0


def test_the_type_dialog_is_given_the_names_already_in_use(qtbot, env, monkeypatch):
    d = designer(qtbot, env, ocr={"mode": "full_page", "document_types": [NIC]})
    seen = {}

    def spy(self):
        seen["taken"] = self._taken
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(DocumentTypeDialog, "exec", spy)
    d.add_type()
    assert seen["taken"] == {"nic"}


def test_the_document_types_box_is_read_only_for_a_centrally_managed_project(qtbot, env):
    d = designer(qtbot, env)
    env.db.set_central_project(d.config["project_id"], 1)
    d.load_selected()
    assert not d.type_box.isEnabled()


# -- the dialog itself ----------------------------------------------------------------
def dialog(qtbot, **kw):
    dlg = DocumentTypeDialog(**kw)
    qtbot.addWidget(dlg)
    return dlg


def test_dialog_requires_a_name_a_pattern_and_a_field(qtbot):
    dlg = dialog(qtbot)
    assert "name" in dlg.problem()
    dlg.name.setText("NIC")
    assert "pattern" in dlg.problem()
    dlg.regex.setText("identity card")
    assert "field" in dlg.problem()
    dlg._append_zone({"name": "nic_number", "regex": "(x)"})
    assert dlg.problem() == ""
    assert dlg.build_type() == {"name": "NIC", "regex": "identity card",
                                "zones": [{"name": "nic_number", "regex": "(x)"}]}


def test_dialog_rejects_a_broken_pattern_and_a_duplicate_name(qtbot):
    dlg = dialog(qtbot, taken_names=("NIC",))
    dlg.name.setText("nic")
    assert "already" in dlg.problem()
    dlg.name.setText("Letter")
    dlg.regex.setText("(")
    dlg._append_zone({"name": "subject", "regex": "(x)"})
    assert "not a valid" in dlg.problem()


def test_dialog_shows_the_problem_and_stays_open_on_ok(qtbot):
    dlg = dialog(qtbot)
    dlg._accept()
    assert dlg.error.text() == "Give the document type a name."
    assert dlg.result() != QDialog.DialogCode.Accepted


def test_dialog_edits_an_existing_type(qtbot):
    dlg = dialog(qtbot, doc_type=NIC)
    assert dlg.name.text() == "NIC" and dlg.zone_list.count() == 1
    dlg.regex.setText("identity")
    dlg._accept()
    assert dlg.result() == QDialog.DialogCode.Accepted
    assert dlg.build_type()["regex"] == "identity"


def test_a_type_remembers_its_own_extraction_mode(qtbot):
    dlg = dialog(qtbot, doc_type=dict(NIC, mode="zone"), full_page=True)
    assert dlg.mode.currentData() == "zone" and dlg.full_page is False
    assert dlg.build_type()["mode"] == "zone"


def test_without_a_mode_the_type_follows_the_project_and_saves_no_mode_key(qtbot):
    dlg = dialog(qtbot, doc_type=NIC, full_page=False)
    assert dlg.mode.currentData() is None
    assert dlg.full_page is False                      # follows the project
    assert "mode" not in dlg.build_type()


def test_choosing_text_patterns_overrides_a_region_project(qtbot):
    dlg = dialog(qtbot, doc_type=NIC, full_page=False)
    dlg.mode.setCurrentIndex(dlg.mode.findData("full_page"))
    assert dlg.full_page is True and dlg.build_type()["mode"] == "full_page"
