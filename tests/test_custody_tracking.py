"""A custody bundle follows its documents: which documents were scanned under
its QR label, and what stage the bundle is at as they go through QC."""
import cv2
import pytest

import core.job_queue as jq
from core.job_queue import Pipeline
from core.rbac import Session, hash_password
from custody.bundle_manager import (bundle_summary, create_bundle, link_document,
                                    record_checkpoint, refresh_for_document,
                                    refresh_stage)
from data.db import Database
from delivery.local_folder import LocalFolderChannel
from export.batch_exporter import export_document
from ocr.tesseract_engine import OCRResult
from tests.test_pipeline import make_profile, wait_for, write_pages

DENSE = "TAX INVOICE Abans Logistics customer shipper vessel voyage amount " * 12


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    d.insert_project("proj-1", "Test Project", {})
    d.insert_batch("b1", "proj-1", "admin")
    yield d
    Session.logout()
    d.close()


def doc(db, doc_id, status="auto_approved", batch="b1", delivery="pending"):
    db.insert_document(doc_id, batch, "proj-1", page_range="1-1", qc_score=90,
                       validation_status=status, extracted_fields={})
    db.update_document(doc_id, delivery_status=delivery)


def stages(db, bundle):
    return [(e["stage"], e["batch_id"]) for e in db.list_custody_events(bundle["bundle_id"])]


def admin(db):
    db.insert_user("u-a", "adm", hash_password("pw"), "admin")
    return Session.login("adm", "pw", db)


# -- the link ------------------------------------------------------------------------------
def test_a_document_is_linked_to_its_bundle_and_listed_with_its_state(db):
    b = create_bundle(db, "proj-1", "Box 1")
    doc(db, "d1", "auto_approved", delivery="delivered_local")
    doc(db, "d2", "pending_qc")
    link_document(db, b["bundle_id"], "d1", "b1")
    link_document(db, b["bundle_id"], "d2", "b1")
    link_document(db, b["bundle_id"], "d2", "b1")               # twice: still one row
    rows = db.list_bundle_documents(b["bundle_id"])
    assert [r["document_id"] for r in rows] == ["d1", "d2"]
    assert db.bundles_of_document("d1") == [b["bundle_id"]]
    assert bundle_summary(db, b["bundle_id"]) == {
        "documents": 2, "approved": 1, "corrected": 0, "rejected": 0,
        "waiting_qc": 1, "delivered": 1}


# -- the stage ------------------------------------------------------------------------------------
def test_the_bundle_moves_to_qc_when_every_document_has_its_decision(db):
    b = create_bundle(db, "proj-1", "Box 1")
    record_checkpoint(db, b["bundle_id"], "scanning", "admin", batch_id="b1")
    doc(db, "d1", "auto_approved")
    doc(db, "d2", "pending_qc")
    for d in ("d1", "d2"):
        link_document(db, b["bundle_id"], d, "b1")
    assert refresh_stage(db, b["bundle_id"], "admin") == []          # d2 still waits
    assert db.get_bundle(b["bundle_id"])["status"] == "scanning"
    db.update_document("d2", validation_status="corrected")          # the reviewer decides
    refresh_for_document(db, "d2", "admin")
    assert db.get_bundle(b["bundle_id"])["status"] == "qc"
    assert ("qc", "b1") in stages(db, b)


def test_the_qc_checkpoint_is_logged_once_per_batch(db):
    b = create_bundle(db, "proj-1", "Box 1")
    doc(db, "d1")
    link_document(db, b["bundle_id"], "d1", "b1")
    refresh_stage(db, b["bundle_id"], "admin")
    refresh_stage(db, b["bundle_id"], "admin")
    assert [s for s, _ in stages(db, b)].count("qc") == 1


def test_a_second_scan_of_the_same_bundle_gets_its_own_qc_step(db):
    db.insert_batch("b2", "proj-1", "admin")
    b = create_bundle(db, "proj-1", "Box 1")
    doc(db, "d1", batch="b1")
    doc(db, "d2", batch="b2")
    link_document(db, b["bundle_id"], "d1", "b1")
    link_document(db, b["bundle_id"], "d2", "b2")
    refresh_stage(db, b["bundle_id"], "admin")
    assert sorted(bt for s, bt in stages(db, b) if s == "qc") == ["b1", "b2"]


def test_a_scan_never_moves_a_bundle_back_out_of_a_physical_stage(db):
    b = create_bundle(db, "proj-1", "Box 1")
    record_checkpoint(db, b["bundle_id"], "storage", "admin")
    doc(db, "d1")
    link_document(db, b["bundle_id"], "d1", "b1")
    assert refresh_stage(db, b["bundle_id"], "admin") == []
    assert db.get_bundle(b["bundle_id"])["status"] == "storage"


# -- through the real pipeline -----------------------------------------------------------------------
def run_pipeline(tmp_path, db, monkeypatch, pages):
    bundle = create_bundle(db, "proj-1", "Box 1")
    profile = make_profile(tmp_path)
    profile["separation"] = {"strategy": ["barcode"], "blank_threshold": 0.02}
    profile["features"]["auto_index"] = False
    jobs = write_pages(tmp_path, ["text"] * len(pages))
    for j in jobs:
        j.batch_id = "b9"
        img = cv2.imread(j.image_path, cv2.IMREAD_UNCHANGED)
        img[5, 5] = 100 + j.page_no
        cv2.imwrite(j.image_path, img)
    monkeypatch.setattr(jq, "decode_barcodes", lambda image: (
        [{"type": "QRCODE", "value": bundle["qr_value"], "rect": (0, 0, 9, 9)}]
        if pages[int(image[5, 5]) - 101] else []))
    pipeline = Pipeline(db, profile, [LocalFolderChannel(profile["delivery"]["local_folder"])],
                        export_document, working_dir=tmp_path / "staging",
                        ocr_fn=lambda image: OCRResult(DENSE, 0.95))
    pipeline.start()
    try:
        pipeline.submit_batch("b9", jobs, operator="admin")
        assert wait_for(lambda: len(db.list_documents("b9")) >= 1)
        wait_for(lambda: all(d["qc_detail"] for d in db.list_documents("b9")))
    finally:
        pipeline.shutdown()
    return bundle


def test_documents_scanned_under_a_label_are_linked_and_the_bundle_reaches_qc(tmp_path, db, monkeypatch):
    # 3 pages carry the label (3 documents); the 4th has none and joins the 3rd
    bundle = run_pipeline(tmp_path, db, monkeypatch, [True, True, True, False])
    docs = db.list_bundle_documents(bundle["bundle_id"])
    assert [d["page_range"] for d in docs] == ["1-1", "2-2", "3-4"]
    names = [s for s, _ in stages(db, bundle)]
    assert names.count("scanning") == 1 and names.count("qc") == 1       # all auto-approved
    assert db.get_bundle(bundle["bundle_id"])["status"] == "qc"


# -- the Custody tab ---------------------------------------------------------------------------------------
def test_the_custody_tab_lists_a_bundles_documents_and_its_summary(qtbot, db):
    from ui.custody_panel import CustodyPanel
    user = admin(db)
    b = create_bundle(db, "proj-1", "Box 1")
    doc(db, "d1", "auto_approved", delivery="delivered_local")
    doc(db, "d2", "pending_qc")
    for d in ("d1", "d2"):
        link_document(db, b["bundle_id"], d, "b1")
    panel = CustodyPanel(db, None, user)
    qtbot.addWidget(panel)
    panel.bundle_list.setCurrentRow(0)
    assert panel.docs_table.rowCount() == 2
    assert panel.docs_table.item(0, 3).text() == "auto_approved"
    text = panel.summary_label.text()
    assert "2 document(s)" in text and "1 approved" in text and "1 waiting for QC" in text
    assert "1 delivered" in text


def test_the_custody_tab_says_so_when_a_bundle_has_no_documents_yet(qtbot, db):
    from ui.custody_panel import CustodyPanel
    user = admin(db)
    create_bundle(db, "proj-1", "Empty box")
    panel = CustodyPanel(db, None, user)
    qtbot.addWidget(panel)
    panel.bundle_list.setCurrentRow(0)
    assert panel.docs_table.rowCount() == 0 and "No documents" in panel.summary_label.text()


def test_the_custody_tab_lists_bundles_of_every_project(qtbot, db):
    from ui.custody_panel import CustodyPanel
    db.insert_project("proj-2", "Other", {})
    user = admin(db)
    create_bundle(db, "proj-1", "A")
    create_bundle(db, "proj-2", "B")
    panel = CustodyPanel(db, None, user)
    qtbot.addWidget(panel)
    assert panel.bundle_list.count() == 2


def test_the_selection_survives_a_refresh(qtbot, db):
    from ui.custody_panel import CustodyPanel
    user = admin(db)
    create_bundle(db, "proj-1", "A")
    b = create_bundle(db, "proj-1", "B")
    panel = CustodyPanel(db, None, user)
    qtbot.addWidget(panel)
    panel._reselect(b["bundle_id"])
    panel.refresh()
    assert panel._selected_bundle_id() == b["bundle_id"]


def test_a_qc_decision_in_the_review_tab_moves_the_bundle_on(qtbot, db):
    from ui.qc_review import QCReviewPanel
    user = admin(db)
    b = create_bundle(db, "proj-1", "Box 1")
    record_checkpoint(db, b["bundle_id"], "scanning", "adm", batch_id="b1")
    doc(db, "d1", "pending_qc")
    link_document(db, b["bundle_id"], "d1", "b1")
    panel = QCReviewPanel(db, None, user)
    qtbot.addWidget(panel)
    panel.queue.setCurrentRow(0)
    panel.reject_btn.click()
    assert db.get_bundle(b["bundle_id"])["status"] == "qc"          # rejected counts as decided
