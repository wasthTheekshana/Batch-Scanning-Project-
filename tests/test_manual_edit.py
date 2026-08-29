"""Tests: manual edit operations, page editor dialog, QC report PDF,
reports panel."""
import json

import cv2
import numpy as np
import pytest

from core.rbac import User
from data.audit import AuditLogger
from data.db import Database
from imaging.manual_edit import (adjust_brightness_contrast, crop_rect,
                                 remove_black_borders, rotate90, rotate_fine,
                                 whiteout_rect)
from tests.conftest import make_text_page

QC_OFFICER = User("q1", "qcuser", "qc_officer")
VIEWER = User("v1", "viewer1", "viewer")
OPERATOR = User("o1", "op1", "operator")


# ---- manual edit operations ---------------------------------------------------

def test_rotate90_changes_dimensions(text_page):
    out = rotate90(text_page, 1)
    assert out.shape[:2] == text_page.shape[:2][::-1]
    assert np.array_equal(rotate90(rotate90(text_page, 1), -1), text_page)
    assert np.array_equal(rotate90(text_page, 2),
                          cv2.rotate(text_page, cv2.ROTATE_180))


def test_rotate_fine_white_fill(text_page):
    out = rotate_fine(text_page, 5.0)
    assert out.shape == text_page.shape
    assert out[0, 0] > 200  # corner filled white, not black


def test_crop_rect_clamps(text_page):
    out = crop_rect(text_page, 100, 100, 200, 300)
    assert out.shape[:2] == (300, 200)
    clamped = crop_rect(text_page, -50, -50, 10_000, 10_000)
    assert clamped.shape == text_page.shape


def test_whiteout_rect(text_page):
    page = text_page.copy()
    page[200:260, 100:900] = 0  # black bar
    out = whiteout_rect(page, 100, 200, 800, 60)
    assert out[230, 500] == 255
    assert page[230, 500] == 0  # source untouched


def test_brightness_contrast(text_page):
    brighter = adjust_brightness_contrast(text_page, brightness=50)
    assert brighter.mean() > text_page.mean()
    # contrast gain needs mid-tones — a pure b/w page is already saturated
    low = (110 + (text_page.astype(np.float32) / 255) * 40).astype(np.uint8)
    higher_contrast = adjust_brightness_contrast(low, contrast=1.5)
    assert higher_contrast.std() > low.std()


def test_remove_black_borders():
    page = make_text_page()
    page[:, :60] = 10          # black scanner border left
    page[-40:, :] = 5          # and bottom
    out, margins = remove_black_borders(page)
    assert margins["left"] >= 55 and margins["bottom"] >= 35
    assert out[:, :50].min() >= 250
    assert out[-30:, :].min() >= 250
    # clean page untouched
    clean, m2 = remove_black_borders(make_text_page())
    assert all(v == 0 for v in m2.values())


# ---- page editor dialog ----------------------------------------------------------

@pytest.fixture()
def editor_env(qtbot, tmp_path):
    db = Database(tmp_path / "ed.db")
    audit = AuditLogger(db, tmp_path / "logs")
    db.insert_project("p1", "P", {})
    db.insert_batch("b1", "p1", "op")
    db.insert_document("d1", "b1", "p1")
    original = tmp_path / "orig.png"
    working = tmp_path / "work.png"
    cv2.imwrite(str(original), make_text_page())
    cv2.imwrite(str(working), make_text_page())
    pages = {"1": {"original_path": str(original),
                   "working_path": str(working)}}
    yield db, audit, pages, working
    audit.close()
    db.close()


def test_editor_rotate_and_save_audited(qtbot, editor_env):
    from ui.page_editor import PageEditDialog
    db, audit, pages, working = editor_env
    dlg = PageEditDialog("d1", pages, audit=audit, username="qcuser")
    qtbot.addWidget(dlg)
    before = cv2.imread(str(working), cv2.IMREAD_UNCHANGED)
    dlg._apply("rotate90", turns=1)
    dlg._save()
    after = cv2.imread(str(working), cv2.IMREAD_UNCHANGED)
    assert after.shape[:2] == before.shape[:2][::-1]

    row = db.conn.execute(
        "SELECT * FROM audit_log WHERE action='manual_edit'").fetchone()
    detail = json.loads(row["detail"])
    assert detail["operations"] == [{"op": "rotate90", "turns": 1}]
    trail = json.loads(db.get_document("d1")["audit_trail"])
    assert trail[0]["action"] == "manual_edit"


def test_editor_whiteout_selection_and_undo(qtbot, editor_env):
    from ui.page_editor import PageEditDialog
    _db, audit, pages, working = editor_env
    dlg = PageEditDialog("d1", pages, audit=audit, username="qcuser")
    qtbot.addWidget(dlg)
    dlg.img[100:200, 100:200] = 0
    dlg._on_selection(100, 100, 100, 100)
    dlg._whiteout_selection()
    assert dlg.img[150, 150] == 255
    assert dlg.ops[-1]["op"] == "whiteout"
    dlg._undo_last()
    assert dlg.img[150, 150] == 0 and not dlg.ops


def test_editor_manual_enhance_logged(qtbot, editor_env):
    from ui.page_editor import PageEditDialog
    _db, audit, pages, _working = editor_env
    dlg = PageEditDialog("d1", pages, audit=audit, username="qcuser")
    qtbot.addWidget(dlg)
    before_mean = dlg.img.mean()
    dlg.brightness.setValue(40)
    dlg._apply_enhance()
    assert dlg.img.mean() > before_mean
    assert dlg.ops[-1] == {"op": "brightness_contrast",
                           "brightness": 40, "contrast": 1.0}


def test_editor_auto_black_borders(qtbot, editor_env):
    from ui.page_editor import PageEditDialog
    _db, audit, pages, _working = editor_env
    dlg = PageEditDialog("d1", pages, audit=audit, username="qcuser")
    qtbot.addWidget(dlg)
    dlg.img[:, :50] = 0
    dlg._auto_black_borders()
    assert dlg.img[:, :40].min() >= 250
    assert dlg.ops[-1]["op"] == "auto_black_borders"


# ---- QC report + reports panel ------------------------------------------------------

@pytest.fixture()
def report_db(tmp_path):
    db = Database(tmp_path / "rep.db")
    db.insert_project("p1", "P", {})
    db.insert_batch("b1", "p1", "op1")
    db.insert_document("d1", "b1", "p1", page_range="1-2", qc_score=95,
                       validation_status="auto_approved",
                       delivery_status="delivered_local")
    db.insert_document("d2", "b1", "p1", page_range="3-3", qc_score=55,
                       validation_status="pending_qc",
                       qc_detail={"flags": ["blur_warn"]},
                       audit_trail=[{"timestamp": "2026-07-15T10:00:00",
                                     "action": "qc", "user": "system",
                                     "detail": {"score": 55}}])
    yield db, tmp_path
    db.close()


def test_batch_statistics(report_db):
    from export.qc_report import batch_statistics
    db, _tmp = report_db
    stats = batch_statistics(db, "b1")
    assert stats["documents"] == 2
    assert stats["avg_score"] == 75.0 and stats["min_score"] == 55
    assert stats["by_status"] == {"auto_approved": 1, "pending_qc": 1}
    assert stats["delivered"] == 1


def test_batch_statistics_distinguishes_rejection_from_rescan(report_db):
    """Both 'reject' and 'rescan' QC decisions collapse to the same
    validation_status='rejected' (ui/qc_review.py), but the original
    decision survives in the audit log's detail.decision -
    rejection_rate/rescan_rate must be computed from that, not from
    validation_status alone."""
    from export.qc_report import batch_statistics
    db, _tmp = report_db
    db.insert_document("d3", "b1", "p1", page_range="4-4", qc_score=40,
                       validation_status="rejected")
    db.insert_document("d4", "b1", "p1", page_range="5-5", qc_score=45,
                       validation_status="rejected")
    db.append_audit("d3", "qc_officer", "qc_review", {"decision": "reject"})
    db.append_audit("d4", "qc_officer", "qc_review", {"decision": "rescan"})
    stats = batch_statistics(db, "b1")
    assert stats["documents"] == 4
    assert stats["rejected_count"] == 1
    assert stats["rescanned_count"] == 1
    assert stats["rejection_rate"] == 25.0   # 1 of 4 documents
    assert stats["rescan_rate"] == 25.0


def test_batch_statistics_uses_latest_qc_decision_per_document(report_db):
    """A document reviewed more than once (e.g. flagged for rescan, then
    rejected outright after the rescan came back bad) counts under its
    LATEST decision, not the first one and not both."""
    from export.qc_report import batch_statistics
    db, _tmp = report_db
    db.insert_document("d3", "b1", "p1", page_range="4-4", qc_score=40,
                       validation_status="rejected")
    db.append_audit("d3", "qc_officer", "qc_review", {"decision": "rescan"})
    db.append_audit("d3", "qc_officer", "qc_review", {"decision": "reject"})
    stats = batch_statistics(db, "b1")
    assert stats["rejected_count"] == 1
    assert stats["rescanned_count"] == 0


def test_batch_statistics_zero_documents_has_zero_rates(tmp_path):
    from export.qc_report import batch_statistics
    db = Database(tmp_path / "empty.db")
    db.insert_project("p1", "P", {})
    db.insert_batch("b1", "p1", "op1")
    stats = batch_statistics(db, "b1")
    assert stats["rejection_rate"] == 0.0
    assert stats["rescan_rate"] == 0.0
    db.close()


def test_write_qc_report_pdf(report_db):
    from export.qc_report import write_qc_report
    db, tmp = report_db
    out = write_qc_report(db, "b1", tmp / "report.pdf")
    data = out.read_bytes()
    assert data[:5] == b"%PDF-" and len(data) > 1500


def test_reports_panel_and_role_gating(qtbot, report_db):
    from ui.reports_panel import ReportsPanel
    db, _tmp = report_db
    panel = ReportsPanel(db, VIEWER)
    qtbot.addWidget(panel)
    assert panel.batch_list.count() == 1
    assert panel.table.rowCount() == 2
    assert "2 documents" in panel.summary.text()
    assert "rejection rate" in panel.summary.text()
    assert "rescan rate" in panel.summary.text()
    assert panel.export_btn.isEnabled()      # viewer can export reports

    qc_panel = ReportsPanel(db, QC_OFFICER)  # report.view but not export
    qtbot.addWidget(qc_panel)
    assert not qc_panel.export_btn.isEnabled()
