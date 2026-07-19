"""Tests for the indexing work queue, auto-extract, and zone editor."""
import json

import cv2
import pytest

from core.rbac import User
from data.audit import AuditLogger
from data.db import Database
from ocr.tesseract_engine import OCRResult
from tests.conftest import make_text_page
from ui.index_editor import IndexEditor

ADMIN = User("a1", "root", "admin")

ZONES = [{"name": "invoice_no", "x": 0, "y": 0, "w": 800, "h": 200,
          "regex": r"(INV-\d+)"}]


@pytest.fixture()
def env(tmp_path):
    db = Database(tmp_path / "idx.db")
    audit = AuditLogger(db, tmp_path / "logs")
    db.insert_project("p1", "P", {})
    db.insert_batch("b1", "p1", "op")
    page = tmp_path / "page.png"
    cv2.imwrite(str(page), make_text_page())
    qc_detail = {"flags": ["low_confidence_index"],
                 "pages": {"1": {"original_path": str(page),
                                 "working_path": str(page)}}}
    db.insert_document("d1", "b1", "p1", page_range="1-1", qc_score=90,
                       validation_status="auto_approved",
                       qc_detail=qc_detail, extracted_fields={})
    db.insert_document("d2", "b1", "p1", page_range="2-2", qc_score=95,
                       validation_status="auto_approved",
                       extracted_fields={"invoice_no": "INV-1"},
                       qc_detail={"flags": [], "pages": {}})
    yield db, audit, tmp_path
    audit.close()
    db.close()


def test_needs_indexing_filter(qtbot, env):
    db, audit, _tmp = env
    editor = IndexEditor(db, ZONES, audit, ADMIN)
    qtbot.addWidget(editor)
    assert editor.doc_list.count() == 1          # only the flagged doc
    editor.filter_combo.setCurrentIndex(1)       # All documents
    assert editor.doc_list.count() == 2


def test_select_loads_fields_and_image(qtbot, env):
    db, audit, _tmp = env
    editor = IndexEditor(db, ZONES, audit, ADMIN)
    qtbot.addWidget(editor)
    editor.doc_list.setCurrentRow(0)
    assert editor.document_id == "d1"
    assert "d1"[:2] in editor.title.text() or "Document" in editor.title.text()
    assert not editor.page_view._item.pixmap().isNull()  # page preview shown


def test_save_updates_fields_clears_flag_and_audits(qtbot, env):
    db, audit, _tmp = env
    editor = IndexEditor(db, ZONES, audit, ADMIN)
    qtbot.addWidget(editor)
    editor.doc_list.setCurrentRow(0)
    editor._inputs["invoice_no"].setText("INV-9999")
    editor.save()
    doc = db.get_document("d1")
    assert json.loads(doc["extracted_fields"])["invoice_no"] == "INV-9999"
    assert "low_confidence_index" not in json.loads(doc["qc_detail"])["flags"]
    row = db.conn.execute(
        "SELECT * FROM audit_log WHERE action='index_edit'").fetchone()
    assert row is not None
    # queue no longer lists it under "needs indexing"
    assert editor.doc_list.count() == 0


def test_auto_extract_fills_fields(qtbot, env, monkeypatch):
    db, audit, _tmp = env
    editor = IndexEditor(db, ZONES, audit, ADMIN)
    qtbot.addWidget(editor)
    editor.doc_list.setCurrentRow(0)
    monkeypatch.setattr(
        "ocr.zone_extractor.ocr_page",
        lambda img, lang="eng": OCRResult(text="No: INV-4242", confidence=0.9))
    editor.auto_extract()
    assert editor._inputs["invoice_no"].text() == "INV-4242"


def test_no_zones_shows_guidance(qtbot, env):
    db, audit, _tmp = env
    editor = IndexEditor(db, [], audit, ADMIN)
    qtbot.addWidget(editor)
    assert "No index zones configured" in editor.hint.text()


def test_zone_dialog_test_zone_shows_raw_and_regex(qtbot, tmp_path,
                                                   monkeypatch):
    from ui.zone_dialog import ZoneDialog
    sample = tmp_path / "sample.png"
    cv2.imwrite(str(sample), make_text_page())
    dlg = ZoneDialog()
    qtbot.addWidget(dlg)
    dlg.load_sample(str(sample))
    dlg._on_region(0, 0, 800, 200)
    monkeypatch.setattr(
        "ocr.tesseract_engine.ocr_page",
        lambda img, lang="eng": OCRResult(text="Invoice No INV-5555",
                                          confidence=0.91))
    dlg.regex.setText(r"(INV-\d+)")
    dlg.test_zone()
    assert "INV-5555" in dlg.result.text()
    assert "will be stored" in dlg.result.text()
    dlg.regex.setText(r"(PO-\d+)")     # non-matching regex is diagnosed
    dlg.test_zone()
    assert "NO MATCH" in dlg.result.text()


def test_auto_extract_prefers_original_geometry(qtbot, env, monkeypatch):
    """Zones are drawn on original pages; extraction must not run on the
    cropped working copy."""
    db, audit, tmp = env
    original = tmp / "orig_big.png"
    working = tmp / "work_small.png"
    cv2.imwrite(str(original), make_text_page())            # 1000x800
    cv2.imwrite(str(working), make_text_page()[:500, :400])  # cropped
    db.update_document("d1", qc_detail={
        "flags": [], "pages": {"1": {"original_path": str(original),
                                     "working_path": str(working)}}})
    seen = {}

    def fake_extract(image, zones, lang="eng", ocr_fn=None):
        seen["shape"] = image.shape
        from ocr.zone_extractor import FieldValue
        return {"invoice_no": FieldValue("INV-1", 0.9)}

    monkeypatch.setattr("ocr.zone_extractor.extract_fields", fake_extract)
    editor = IndexEditor(db, ZONES, audit, ADMIN)
    qtbot.addWidget(editor)
    editor.filter_combo.setCurrentIndex(1)
    for i in range(editor.doc_list.count()):
        if "d1"[:8] in editor.doc_list.item(i).text():
            editor.doc_list.setCurrentRow(i)
            break
    editor.auto_extract()
    assert seen["shape"][:2] == (1000, 800)  # original, not the crop


def test_zone_dialog_builds_zone(qtbot):
    from ui.zone_dialog import ZoneDialog
    dlg = ZoneDialog()
    qtbot.addWidget(dlg)
    dlg.name.setText("invoice_no")
    dlg.regex.setText(r"(INV-\d+)")
    dlg._on_region(10, 20, 300, 80)
    zone = dlg.build_zone()
    assert zone == {"name": "invoice_no", "x": 10, "y": 20,
                    "w": 300, "h": 80, "regex": r"(INV-\d+)"}
    dlg.name.setText("")                 # incomplete → refuses to build
    assert dlg.build_zone() is None


def test_designer_zones_round_trip(qtbot, tmp_path):
    from core.profile_manager import ProfileManager
    from ui.workflow_designer import WorkflowDesigner
    db = Database(tmp_path / "dz.db")
    pm = ProfileManager(tmp_path / "projects", db)
    pm.create("Zoned Project")
    designer = WorkflowDesigner(db, pm, ADMIN)
    qtbot.addWidget(designer)
    designer._append_zone({"name": "invoice_no", "x": 5, "y": 6,
                           "w": 100, "h": 40, "regex": r"INV-\d+"})
    cfg = designer.collect_config()
    assert cfg["ocr"]["zones"][0]["name"] == "invoice_no"
    saved = pm.save(cfg)                 # validates against project schema
    assert saved["ocr"]["zones"][0]["w"] == 100
    designer.config = saved
    designer._bind_config()
    assert designer.zone_list.count() == 1
    db.close()
