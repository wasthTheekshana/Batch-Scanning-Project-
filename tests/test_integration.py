"""End-to-end integration test (HLD Phase 1 week 6).

Synthesises a batch, runs it through the real pipeline with real
export and local delivery, and verifies every layer: separation, QC
routing, rule engine, export, delivery, jobs, batches, audit.
"""
import json
import time
from pathlib import Path

import cv2
import pytest

from core.job_queue import Pipeline
from core.models import ScanJob
from core.profile_manager import ProfileManager
from data.audit import AuditLogger
from data.db import Database
from delivery.registry import active_channels
from export.batch_exporter import export_document
from tests.conftest import make_blank_page, make_text_page


def wait_for(predicate, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.15)
    return False


@pytest.fixture()
def env(tmp_path):
    db = Database(tmp_path / "int.db")
    audit = AuditLogger(db, tmp_path / "logs")
    pm = ProfileManager(tmp_path / "projects", db)
    cfg = pm.create("Integration Project")
    cfg["separation"] = {"strategy": ["blank_page"], "blank_threshold": 0.02}
    cfg["qc"]["stages"] = ["margin", "color", "advanced"]  # no OSD needed
    cfg["export"]["format"] = "image_pdf"
    cfg["export"]["output_path"] = str(tmp_path / "exports")
    cfg["delivery"]["local_folder"] = {"enabled": True,
                                       "path": str(tmp_path / "delivered")}
    cfg = pm.save(cfg)
    yield db, audit, cfg, tmp_path
    audit.close()
    db.close()


def test_full_system_flow(env):
    db, audit, cfg, tmp_path = env
    pipeline = Pipeline(db, cfg,
                        channels=active_channels(cfg["delivery"]),
                        export_fn=export_document, audit=audit,
                        working_dir=tmp_path / "staging")
    pipeline.start()
    try:
        # batch: [text text] BLANK [blurred] → doc1 PASS, doc2 FAIL
        images = [make_text_page(), make_text_page(), make_blank_page(),
                  cv2.GaussianBlur(make_text_page(), (31, 31), 12)]
        jobs = []
        for i, img in enumerate(images, start=1):
            p = tmp_path / f"page{i}.png"
            cv2.imwrite(str(p), img)
            jobs.append(ScanJob(batch_id="B1", project_id=cfg["project_id"],
                                page_no=i, image_path=str(p)))
        pipeline.submit_batch("B1", jobs, operator="op1")

        assert wait_for(lambda: len(db.list_documents("B1")) == 2 and any(
            d["delivery_status"] == "delivered_local"
            for d in db.list_documents("B1"))), "pipeline did not finish"
    finally:
        pipeline.shutdown()

    docs = {d["page_range"]: d for d in db.list_documents("B1")}
    good, blurred = docs["1-2"], docs["4-4"]

    # separation + QC routing
    assert good["validation_status"] == "auto_approved"
    assert good["qc_score"] >= 80
    assert blurred["validation_status"] == "pending_qc"
    assert blurred["qc_score"] < 80  # WARN or FAIL — held for review either way

    # export + delivery for the good document only
    assert Path(good["storage_path"]).exists()
    assert good["delivery_status"] == "delivered_local"
    assert json.loads(good["delivery_detail"])["local_folder"]["success"]
    assert blurred["storage_path"] is None
    delivered = list((tmp_path / "delivered").glob("*.pdf"))
    assert len(delivered) == 1

    # jobs all complete (crash-recovery table clean)
    assert not db.get_incomplete_jobs()

    # audit trail populated in DB, file, and document JSON
    actions = {r["action"] for r in
               db.conn.execute("SELECT action FROM audit_log").fetchall()}
    assert {"rule_engine", "export", "deliver"} <= actions
    assert (tmp_path / "logs" / "audit.log").exists()
    trail = json.loads(good["audit_trail"])
    assert any(t["action"] == "export" for t in trail)

    # simulate operator approval of the blurred doc → becomes exportable
    db.update_document(blurred["document_id"], validation_status="corrected")
    pending = db.list_documents(validation_status="pending_qc")
    assert not pending
