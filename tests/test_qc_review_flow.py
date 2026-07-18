"""Regression tests for the QC review flow: held documents must appear
in the panel (incl. after restart), show before/after images, and
'Accept → export' must actually export and deliver."""
import json
from pathlib import Path

import cv2
import pytest

from core.job_queue import Pipeline
from core.rbac import User
from data.db import Database
from delivery.local_folder import LocalFolderChannel
from export.batch_exporter import export_document
from tests.test_pipeline import build_pipeline, make_profile, wait_for, write_pages
from ui.qc_review import QCReviewPanel

QC_OFFICER = User("q1", "qcuser", "qc_officer")


@pytest.fixture()
def held_doc_env(tmp_path):
    """Run a batch with one blurred page so one document is held."""
    db = Database(tmp_path / "qc.db")
    profile = make_profile(tmp_path)
    pipeline = build_pipeline(tmp_path, db, profile)
    pipeline.start()
    try:
        jobs = write_pages(tmp_path, ["text", "blank", "blur"])
        pipeline.submit_batch("batch-1", jobs)  # write_pages hardcodes batch-1
        assert wait_for(lambda: len(db.list_documents(
            validation_status="pending_qc")) == 1)
    finally:
        pipeline.shutdown()
    yield db, profile, tmp_path
    db.close()


def test_qc_detail_records_page_image_paths(held_doc_env):
    db, _profile, _tmp = held_doc_env
    doc = db.list_documents(validation_status="pending_qc")[0]
    pages = json.loads(doc["qc_detail"])["pages"]
    info = next(iter(pages.values()))
    assert Path(info["original_path"]).exists()
    assert Path(info["working_path"]).exists()


def test_panel_populates_on_construction(qtbot, held_doc_env):
    """Regression: queue was empty until a live qc_ready event."""
    db, _profile, _tmp = held_doc_env
    panel = QCReviewPanel(db, None, QC_OFFICER)   # fresh session, no events
    qtbot.addWidget(panel)
    assert panel.queue.count() == 1


def test_panel_shows_before_after_images(qtbot, held_doc_env):
    db, _profile, _tmp = held_doc_env
    panel = QCReviewPanel(db, None, QC_OFFICER)
    qtbot.addWidget(panel)
    panel.queue.setCurrentRow(0)
    assert not panel.before_view._item.pixmap().isNull()
    assert not panel.after_view._item.pixmap().isNull()


def test_accept_exports_and_delivers(qtbot, held_doc_env):
    db, profile, tmp_path = held_doc_env
    # fresh pipeline session, as after an app restart
    pipeline = Pipeline(db, profile,
                        [LocalFolderChannel(profile["delivery"]["local_folder"])],
                        export_document, working_dir=tmp_path / "staging")
    pipeline.start()
    try:
        panel = QCReviewPanel(db, None, QC_OFFICER, pipeline=pipeline)
        qtbot.addWidget(panel)
        panel.queue.setCurrentRow(0)
        doc_id = panel.current_document_id()
        panel._action("accept")
        assert panel.queue.count() == 0           # left the review queue
        assert wait_for(lambda: db.get_document(doc_id)["delivery_status"]
                        == "delivered_local")
    finally:
        pipeline.shutdown()
    doc = db.get_document(doc_id)
    assert doc["validation_status"] == "corrected"
    assert doc["storage_path"] and Path(doc["storage_path"]).exists()
    delivered = list((tmp_path / "delivered").glob("*.pdf"))
    assert len(delivered) >= 1


def test_all_documents_filter_shows_passed_docs(qtbot, held_doc_env):
    """Admin can open any document (and its page editor), not just the
    WARN/FAIL review queue."""
    db, _profile, _tmp = held_doc_env
    panel = QCReviewPanel(db, None, QC_OFFICER)
    qtbot.addWidget(panel)
    assert panel.queue.count() == 1          # default: review queue only
    panel.filter_combo.setCurrentIndex(1)    # All documents
    assert panel.queue.count() == 2          # passed doc now visible too
    panel.queue.setCurrentRow(0)
    assert panel.editable_pages(panel.current_document_id())


def test_reject_does_not_export(qtbot, held_doc_env):
    db, profile, tmp_path = held_doc_env
    panel = QCReviewPanel(db, None, QC_OFFICER)
    qtbot.addWidget(panel)
    panel.queue.setCurrentRow(0)
    doc_id = panel.current_document_id()
    panel._action("reject")
    doc = db.get_document(doc_id)
    assert doc["validation_status"] == "rejected"
    assert doc["storage_path"] is None
