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


FULL_PAGE_ZONES = [{"name": "invoice_no", "regex": r"Invoice No:\s*(\S+)"}]


def test_auto_extract_full_page_mode_uses_extract_fields_from_text(
        qtbot, env, monkeypatch):
    """Full-page mode zones have no x/y/w/h — extract_fields (the
    zone-cropping path) would skip them entirely. auto_extract() must
    route to extract_fields_from_text via one whole-page OCR call
    instead."""
    db, audit, _tmp = env
    editor = IndexEditor(db, FULL_PAGE_ZONES, audit, ADMIN, full_page=True)
    qtbot.addWidget(editor)
    editor.doc_list.setCurrentRow(0)
    monkeypatch.setattr(
        "ocr.tesseract_engine.ocr_page",
        lambda img, lang="eng": OCRResult(
            text="Header stuff\nInvoice No: INV-4242\nfooter",
            confidence=0.9))
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


@pytest.mark.parametrize("raw_text,expected", [
    ("Invoice No. :CP11407366", r":\s*(\S+)"),      # single token
    ("Territory : SP04-Ashan Shanuka", r":\s*(.+)"),  # value has a space
    ("no colon here", None),
    ("Label :", None),                               # colon but no value
])
def test_suggest_label_regex(raw_text, expected):
    from ui.zone_dialog import suggest_label_regex
    assert suggest_label_regex(raw_text) == expected


def test_zone_dialog_suggest_regex_fills_and_tests(qtbot, tmp_path, monkeypatch):
    """The 'Suggest regex' button fills in a regex from the raw OCR read
    and immediately shows what it would extract, without the admin
    having to hand-write the pattern (the fix applied for the Invoice
    No / Territory zones, now available in the dialog itself)."""
    from ui.zone_dialog import ZoneDialog
    sample = tmp_path / "sample.png"
    cv2.imwrite(str(sample), make_text_page())
    dlg = ZoneDialog()
    qtbot.addWidget(dlg)
    dlg.load_sample(str(sample))
    dlg._on_region(0, 0, 800, 200)
    monkeypatch.setattr(
        "ocr.tesseract_engine.ocr_page",
        lambda img, lang="eng": OCRResult(
            text="Territory : SP04-Ashan Shanuka", confidence=0.66))
    dlg.suggest_regex()
    assert dlg.regex.text() == r":\s*(.+)"
    assert "SP04-Ashan Shanuka" in dlg.result.text()
    assert "will be stored" in dlg.result.text()


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


def test_zone_scaling_is_resolution_independent():
    """A zone drawn on a small sample must land on the same relative
    region of a higher-resolution page."""
    from ocr.zone_extractor import extract_fields, scaled_rect

    zone = {"name": "f", "x": 100, "y": 50, "w": 200, "h": 100,
            "ref_w": 800, "ref_h": 1000}
    assert scaled_rect(zone, 1600, 2000) == (200, 100, 400, 200)
    assert scaled_rect(zone, 800, 1000) == (100, 50, 200, 100)
    # zone without ref stays absolute (backwards compatible)
    legacy = {"name": "f", "x": 100, "y": 50, "w": 200, "h": 100}
    assert scaled_rect(legacy, 1600, 2000) == (100, 50, 200, 100)

    # end-to-end: big page, dark patch only inside the scaled region
    import numpy as np
    page = np.full((2000, 1600), 255, np.uint8)
    page[100:300, 200:600] = 0
    seen = {}

    def fake_ocr(region):
        seen["shape"] = region.shape
        return OCRResult(text="HIT" if region.min() < 128 else "",
                         confidence=0.9)

    fields = extract_fields(page, [zone], ocr_fn=fake_ocr)
    assert fields["f"].value == "HIT"
    assert seen["shape"] == (200, 400)  # the scaled rect, not the raw one


def test_auto_extract_searches_all_pages(qtbot, env, monkeypatch):
    """A field on page 2 must still be found."""
    db, audit, tmp = env
    page1 = tmp / "p1.png"; page2 = tmp / "p2.png"
    cv2.imwrite(str(page1), make_text_page())
    cv2.imwrite(str(page2), make_text_page())
    db.update_document("d1", qc_detail={
        "flags": [], "pages": {
            "1": {"original_path": str(page1)},
            "2": {"original_path": str(page2)}}})
    calls = {"n": 0}

    def fake_extract(image, zones, lang="eng", ocr_fn=None):
        calls["n"] += 1
        from ocr.zone_extractor import FieldValue
        # page 1 reads nothing; page 2 has the value
        value = "INV-2ND" if calls["n"] == 2 else ""
        return {"invoice_no": FieldValue(value, 0.9)}

    monkeypatch.setattr("ocr.zone_extractor.extract_fields", fake_extract)
    editor = IndexEditor(db, ZONES, audit, ADMIN)
    qtbot.addWidget(editor)
    editor.filter_combo.setCurrentIndex(1)
    for i in range(editor.doc_list.count()):
        if "d1" in editor.doc_list.item(i).text():
            editor.doc_list.setCurrentRow(i)
            break
    editor.auto_extract()
    assert editor._inputs["invoice_no"].text() == "INV-2ND"
    assert "page 2" in editor.hint.text()


def test_zone_dialog_loads_pdf_sample(qtbot, tmp_path):
    pytest.importorskip("fitz", reason="PyMuPDF not installed")
    import img2pdf
    from ui.zone_dialog import ZoneDialog
    src = tmp_path / "page.png"
    cv2.imwrite(str(src), make_text_page())
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(img2pdf.convert([str(src)]))
    dlg = ZoneDialog()
    qtbot.addWidget(dlg)
    dlg.load_sample(str(pdf))
    assert dlg._sample is not None
    # rendered at 300 DPI → larger than the 800px source, ref recorded
    dlg.name.setText("f")
    dlg._on_region(0, 0, 100, 50)
    zone = dlg.build_zone()
    assert zone["ref_w"] == dlg._sample.shape[1]
    assert zone["ref_h"] == dlg._sample.shape[0]


def test_zone_dialog_records_sample_reference(qtbot, tmp_path):
    from ui.zone_dialog import ZoneDialog
    sample = tmp_path / "s.png"
    cv2.imwrite(str(sample), make_text_page())  # 1000h x 800w
    dlg = ZoneDialog()
    qtbot.addWidget(dlg)
    dlg.load_sample(str(sample))
    dlg.name.setText("invoice_no")
    dlg._on_region(10, 20, 300, 80)
    zone = dlg.build_zone()
    assert zone["ref_w"] == 800 and zone["ref_h"] == 1000
    # re-editing without reloading a sample keeps the reference
    dlg2 = ZoneDialog(zone)
    qtbot.addWidget(dlg2)
    zone2 = dlg2.build_zone()
    assert zone2["ref_w"] == 800 and zone2["ref_h"] == 1000


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


def test_zone_dialog_full_page_build_zone_without_region(qtbot):
    from ui.zone_dialog import ZoneDialog
    dlg = ZoneDialog(full_page=True)
    qtbot.addWidget(dlg)
    dlg.name.setText("invoice_no")
    dlg.regex.setText(r"Invoice No:\s*(\S+)")
    zone = dlg.build_zone()
    assert zone == {"name": "invoice_no", "regex": r"Invoice No:\s*(\S+)"}


def test_zone_dialog_full_page_requires_regex(qtbot):
    """A full-page zone with no regex could never produce a value
    (extract_fields_from_text skips it silently) — build_zone() must
    refuse to save it, the same way it refuses a nameless zone."""
    from ui.zone_dialog import ZoneDialog
    dlg = ZoneDialog(full_page=True)
    qtbot.addWidget(dlg)
    dlg.name.setText("invoice_no")
    assert dlg.build_zone() is None
    dlg.regex.setText(r"Invoice No:\s*(\S+)")
    assert dlg.build_zone() == {"name": "invoice_no",
                                "regex": r"Invoice No:\s*(\S+)"}


def test_zone_dialog_full_page_disables_region_controls(qtbot):
    from ui.zone_dialog import ZoneDialog
    dlg = ZoneDialog(full_page=True)
    qtbot.addWidget(dlg)
    assert not dlg.x.isEnabled()


def test_zone_dialog_zone_mode_still_requires_region(qtbot):
    from ui.zone_dialog import ZoneDialog
    dlg = ZoneDialog()  # full_page defaults to False
    qtbot.addWidget(dlg)
    dlg.name.setText("invoice_no")
    assert dlg.build_zone() is None  # no region dragged, w/h are 0


def test_zone_dialog_opens_on_geometry_less_zone(qtbot):
    # a template-derived zone may define only name/regex (coordinates are
    # page-layout specific and don't transfer across projects) - opening
    # Edit… on it must not crash, and should default the region to 0.
    from ui.zone_dialog import ZoneDialog
    zone = {"name": "invoice_no", "regex": r"(INV-\d+)"}
    dlg = ZoneDialog(zone)
    qtbot.addWidget(dlg)
    assert dlg.x.value() == 0 and dlg.y.value() == 0
    assert dlg.w.value() == 0 and dlg.h.value() == 0
    assert dlg.name.text() == "invoice_no"


def test_zone_dialog_shows_real_geometry(qtbot):
    # a zone that DOES carry a captured region must still display its real
    # values, not the geometry-less fallback.
    from ui.zone_dialog import ZoneDialog
    zone = {"name": "invoice_no", "x": 12, "y": 34, "w": 56, "h": 78,
           "regex": r"(INV-\d+)"}
    dlg = ZoneDialog(zone)
    qtbot.addWidget(dlg)
    assert (dlg.x.value(), dlg.y.value(), dlg.w.value(), dlg.h.value()) == \
        (12, 34, 56, 78)


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


def test_document_is_indexed_with_its_own_projects_zones(qtbot, env):
    """The editor must not use the first project's zones for every
    document: selecting a document switches the form to that document's
    project, and the needs-indexing filter judges it by those zones."""
    db, audit, _tmp = env
    own = [{"name": "other_field", "regex": r"X"}]
    seen = []

    def lookup(project_id):
        seen.append(project_id)
        return (own, True) if project_id == "p1" else None

    editor = IndexEditor(db, ZONES, audit, ADMIN, zones_for_project=lookup)
    qtbot.addWidget(editor)
    editor.doc_list.setCurrentRow(0)
    assert editor.document_id == "d1"
    assert list(editor._inputs) == ["other_field"]
    assert editor.full_page is True
    assert "p1" in seen


def test_unknown_project_keeps_the_current_zones(qtbot, env):
    db, audit, _tmp = env
    editor = IndexEditor(db, ZONES, audit, ADMIN,
                         zones_for_project=lambda pid: None)
    qtbot.addWidget(editor)
    editor.doc_list.setCurrentRow(0)
    assert list(editor._inputs) == ["invoice_no"]
