"""A custody QR printed on every document page (a real case: the same bundle
label on each invoice) must not make the pages disappear. Only a label SHEET
(a QR with hardly any other text) is left out of the documents."""
import json

import cv2
import pytest

import core.job_queue as jq
from core.job_queue import Pipeline
from custody.bundle_manager import create_bundle
from data.db import Database
from delivery.local_folder import LocalFolderChannel
from export.batch_exporter import export_document
from ocr.tesseract_engine import OCRResult
from tests.test_pipeline import make_profile, wait_for, write_pages

DENSE = "TAX INVOICE Abans Logistics customer shipper vessel voyage amount " * 12
SPARSE = "Bundle dok/com/001"


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "c.db")
    d.insert_project("proj-1", "Test Project", {})
    yield d
    d.close()


def run(tmp_path, db, monkeypatch, pages, strategy=("barcode",), wait_docs=True):
    """pages: [(has_custody_qr, text)] in scan order. Returns (documents, events)."""
    bundle = create_bundle(db, "proj-1", "Box 1")
    profile = make_profile(tmp_path)
    profile["separation"] = {"strategy": list(strategy), "blank_threshold": 0.02,
                             "fixed_pages": 1}
    profile["features"]["auto_index"] = False
    texts = {}
    jobs = write_pages(tmp_path, ["text"] * len(pages))
    for j in jobs:
        j.batch_id = "batch-1"
        img = cv2.imread(j.image_path, cv2.IMREAD_UNCHANGED)
        img[5, 5] = 100 + j.page_no                  # stamp: survives whatever order
        cv2.imwrite(j.image_path, img)
        texts[100 + j.page_no] = pages[j.page_no - 1]

    def fake_decode(image):
        has_qr, _t = texts[int(image[5, 5])]
        return ([{"type": "QRCODE", "value": bundle["qr_value"], "rect": (0, 0, 9, 9)}]
                if has_qr else [])
    monkeypatch.setattr(jq, "decode_barcodes", fake_decode)
    pipeline = Pipeline(db, profile, [LocalFolderChannel(profile["delivery"]["local_folder"])],
                        export_document, working_dir=tmp_path / "staging",
                        ocr_fn=lambda image: OCRResult(texts[int(image[5, 5])][1], 0.95))
    events = []
    pipeline.subscribe(events.append)
    pipeline.start()
    try:
        pipeline.submit_batch("batch-1", jobs, operator="admin")
        if wait_docs:
            assert wait_for(lambda: len(db.list_documents("batch-1")) >= 1)
            wait_for(lambda: all(d["qc_detail"] for d in db.list_documents("batch-1")))
        else:
            assert wait_for(lambda: any(e["stage"] == "separation" for e in events))
    finally:
        pipeline.shutdown()
    return bundle, db.list_documents("batch-1"), events


def page_counts(docs):
    return sorted(len(json.loads(d["qc_detail"])["pages"]) for d in docs)


def test_invoices_that_carry_a_custody_qr_are_kept_as_document_pages(tmp_path, db, monkeypatch):
    bundle, docs, _ = run(tmp_path, db, monkeypatch,
                          [(True, DENSE), (True, DENSE), (True, DENSE)])
    assert page_counts(docs) == [3]                          # nothing was thrown away
    barcode = json.loads(docs[0]["extracted_fields"]).get("barcode", "")
    assert barcode != bundle["qr_value"]                     # the bundle is not the document's id


def test_the_custody_checkpoint_is_logged_once_per_batch_not_once_per_page(tmp_path, db, monkeypatch):
    bundle, _docs, _ = run(tmp_path, db, monkeypatch, [(True, DENSE)] * 3)
    stages = [r["stage"] for r in db.conn.execute(
        "select stage from custody_events where bundle_id=?", (bundle["bundle_id"],))]
    assert stages.count("scanning") == 1


def test_a_real_label_sheet_is_still_left_out(tmp_path, db, monkeypatch):
    bundle, docs, _ = run(tmp_path, db, monkeypatch,
                          [(True, SPARSE), (False, DENSE), (False, DENSE)])
    assert page_counts(docs) == [2]                          # the sheet is not in the document


def test_a_batch_that_makes_no_documents_tells_the_operator(tmp_path, db, monkeypatch):
    _b, docs, events = run(tmp_path, db, monkeypatch,
                           [(True, SPARSE), (True, SPARSE)], wait_docs=False)
    assert docs == []
    empty = [e for e in events if e["stage"] == "batch" and e["status"] == "no_documents"]
    assert len(empty) == 1 and empty[0]["batch_id"] == "batch-1"
    assert empty[0]["pages"] == 2 and "custody_label" in empty[0]["reasons"].values()
    assert db.conn.execute("select status from batches where batch_id='batch-1'").fetchone()[0] \
        == "complete"                                        # not stuck on qc_pending


def test_the_window_explains_an_empty_batch(qtbot, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from core.event_bus import EventBus
    from core.profile_manager import ProfileManager
    from core.rbac import Session
    from ui.main_window import MainWindow
    d = Database(tmp_path / "w.db")
    Session.login("admin", "admin", d)
    bus = EventBus()
    win = MainWindow(d, ProfileManager(tmp_path / "profiles"), bus,
                     staging_dir=tmp_path / "staging")
    qtbot.addWidget(win)
    shown = []
    monkeypatch.setattr(QMessageBox, "show", lambda self: shown.append(self.text()))
    bus.publish_pipeline_event({"stage": "batch", "status": "no_documents",
                                "batch_id": "b1", "pages": 8,
                                "reasons": {i: "custody_label" for i in range(1, 9)}})
    assert len(shown) == 1
    assert "8 scanned page(s)" in shown[0] and "8 custody label sheet" in shown[0]
    Session.logout()
    d.close()
