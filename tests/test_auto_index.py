"""Tests for auto-index zone extraction, duplicate detection, and key
export — the Phase 1/2 features now wired into the pipeline."""
import json

import pytest

from data.db import Database
from ocr.tesseract_engine import OCRResult
from tests.test_pipeline import (build_pipeline, make_profile, wait_for,
                                 write_pages)


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "ai.db")
    yield d
    d.close()


def fake_ocr(region):
    """Deterministic OCR stand-in: any dark region 'reads' the invoice no."""
    import numpy as np
    dark = region.size and np.asarray(region).min() < 128
    return OCRResult(text="INV-7777" if dark else "", confidence=0.95)


def run_batch(tmp_path, db, profile, specs, batch_id="batch-1"):
    from core.job_queue import Pipeline
    from delivery.local_folder import LocalFolderChannel
    from export.batch_exporter import export_document
    pipeline = Pipeline(
        db, profile,
        [LocalFolderChannel(profile["delivery"]["local_folder"])],
        export_document, ocr_fn=fake_ocr,
        working_dir=tmp_path / "staging")
    pipeline.start()
    try:
        jobs = write_pages(tmp_path, specs)
        for j in jobs:
            j.batch_id = batch_id
        pipeline.submit_batch(batch_id, jobs)
        assert wait_for(lambda: len(db.list_documents(batch_id)) >= 1
                        and all(d["validation_status"] != "pending_qc"
                                or d["qc_detail"]
                                for d in db.list_documents(batch_id)))
    finally:
        pipeline.shutdown()
    return db.list_documents(batch_id)


def indexed_profile(tmp_path, **over):
    profile = make_profile(tmp_path)
    profile["features"]["auto_index"] = True
    profile["ocr"]["zones"] = [
        {"name": "invoice_no", "x": 0, "y": 0, "w": 800, "h": 200,
         "regex": r"(INV-\d+)"}]
    profile["ocr"]["confidence_threshold"] = 0.75
    profile.update(over)
    return profile


# ---- auto-index ------------------------------------------------------------------

def test_zone_fields_populate_extracted_fields(tmp_path, db):
    docs = run_batch(tmp_path, db, indexed_profile(tmp_path), ["text"])
    fields = json.loads(docs[0]["extracted_fields"])
    assert fields["invoice_no"] == "INV-7777"


def test_low_confidence_flagged(tmp_path, db):
    profile = indexed_profile(tmp_path)
    profile["ocr"]["confidence_threshold"] = 0.99  # above fake's 0.95
    docs = run_batch(tmp_path, db, profile, ["text"])
    detail = json.loads(docs[0]["qc_detail"])
    assert "low_confidence_index" in detail["flags"]


def test_auto_index_off_extracts_nothing(tmp_path, db):
    profile = indexed_profile(tmp_path)
    profile["features"]["auto_index"] = False
    docs = run_batch(tmp_path, db, profile, ["text"])
    assert json.loads(docs[0]["extracted_fields"]) == {}


# ---- duplicate detection -----------------------------------------------------------

def test_duplicate_batch_flagged_and_linked(tmp_path, db):
    profile = indexed_profile(tmp_path)
    profile["features"]["duplicate_check"] = True
    first = run_batch(tmp_path, db, profile, ["text"], batch_id="batch-1")
    assert first[0]["duplicate_of"] is None

    # same page scanned again → duplicate of the first document
    second = run_batch(tmp_path, db, profile, ["text"], batch_id="batch-2")
    dup = second[0]
    assert dup["duplicate_of"] == first[0]["document_id"]
    assert dup["validation_status"] == "pending_qc"
    assert "duplicate" in json.loads(dup["qc_detail"])["flags"]


def test_duplicate_check_off_no_link(tmp_path, db):
    profile = indexed_profile(tmp_path)
    profile["features"]["duplicate_check"] = False
    run_batch(tmp_path, db, profile, ["text"], batch_id="batch-1")
    second = run_batch(tmp_path, db, profile, ["text"], batch_id="batch-2")
    assert second[0]["duplicate_of"] is None


# ---- master key export --------------------------------------------------------------

def test_key_export_writes_hex_and_audits(qtbot, db, tmp_path, monkeypatch):
    from core.rbac import User
    from data.backup import BackupService
    from ui.admin_panel import BackupTab

    key = b"\x01" * 32
    svc = BackupService(db, [], tmp_path / "b", key)
    tab = BackupTab(svc, User("a", "root", "admin"))
    qtbot.addWidget(tab)
    assert tab.export_key_btn.isEnabled()

    out = tmp_path / "exported-key.txt"
    monkeypatch.setattr(
        "ui.admin_panel.QFileDialog.getSaveFileName",
        staticmethod(lambda *a, **k: (str(out), "")))
    monkeypatch.setattr(
        "ui.admin_panel.QMessageBox.warning",
        staticmethod(lambda *a, **k: None))
    tab.export_key()
    assert out.read_text(encoding="utf-8") == key.hex()
    if __import__("os").name == "nt":
        # ACL restricted to current user: inherited entries stripped
        acl = __import__("subprocess").run(
            ["icacls", str(out)], capture_output=True, text=True).stdout
        assert "(I)" not in acl  # no inherited ACEs remain
    row = db.conn.execute(
        "SELECT * FROM audit_log WHERE action='key_export'").fetchone()
    assert row is not None

    # exported hex actually restores an archive (clean-machine path)
    from data.backup import restore_backup
    archive = svc.run_backup()
    restored = restore_backup(archive, tmp_path / "restore",
                              bytes.fromhex(out.read_text(encoding="utf-8")))
    assert restored.exists()


def test_key_export_disabled_for_non_admin(qtbot, db, tmp_path):
    from core.rbac import User
    from data.backup import BackupService
    from ui.admin_panel import BackupTab
    svc = BackupService(db, [], tmp_path / "b", b"k" * 32)
    tab = BackupTab(svc, User("v", "view", "viewer"))
    qtbot.addWidget(tab)
    assert not tab.export_key_btn.isEnabled()
