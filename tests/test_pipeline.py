import time
from pathlib import Path

import cv2
import pytest

from core.job_queue import Pipeline
from core.models import ScanJob
from data.db import Database
from delivery.local_folder import LocalFolderChannel
from export.batch_exporter import export_document
from tests.conftest import make_blank_page, make_text_page


def make_profile(tmp_path, **overrides):
    profile = {
        "project_id": "proj-1", "name": "Test Project", "version": 1,
        "status": "active",
        "scanner": {"dpi": 300, "color": "grayscale", "duplex": False,
                    "sdk": "file"},
        "enhancement_profile": {"deskew": False, "crop": False, "clahe": False,
                                "despeckle": False, "whiten_bg": False,
                                "target_dpi": 300},
        "separation": {"strategy": ["blank_page"], "blank_threshold": 0.02},
        "qc": {"enabled": True, "stages": ["margin", "advanced"],
               "blur_warn_threshold": 80, "blur_fail_threshold": 30,
               "dpi_warn": 200, "dpi_fail": 150, "pass_score": 80},
        "ocr": {"lang": "eng", "zones": [], "confidence_threshold": 0.75},
        "export": {"format": "image_pdf",
                   "filename_template": "{batch_id}_{counter:04d}",
                   "output_path": str(tmp_path / "exports")},
        "delivery": {"local_folder": {"enabled": True,
                                      "path": str(tmp_path / "delivered")},
                     "sftp": {"enabled": False}, "cd_dvd": {"enabled": False}},
        "features": {"auto_index": False, "duplicate_check": False},
        "rules": {"rule_set_id": "rs", "version": 1, "rules": []},
    }
    profile.update(overrides)
    return profile


def write_pages(tmp_path, specs):
    """specs: list of 'text'|'blank'|'blur'; returns list of ScanJob."""
    jobs = []
    for i, kind in enumerate(specs, start=1):
        if kind == "blank":
            img = make_blank_page()
        elif kind == "blur":
            img = cv2.GaussianBlur(make_text_page(), (31, 31), 12)
        else:
            img = make_text_page()
        p = tmp_path / f"scan_{i}.png"
        cv2.imwrite(str(p), img)
        jobs.append(ScanJob(batch_id="batch-1", project_id="proj-1",
                            page_no=i, image_path=str(p)))
    return jobs


def build_pipeline(tmp_path, db, profile):
    channel = LocalFolderChannel(profile["delivery"]["local_folder"])
    return Pipeline(db, profile, [channel], export_document,
                    working_dir=tmp_path / "staging")


def wait_for(predicate, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "p.db")
    yield d
    d.close()


def test_batch_flows_end_to_end(tmp_path, db):
    profile = make_profile(tmp_path)
    pipeline = build_pipeline(tmp_path, db, profile)
    events = []
    pipeline.subscribe(events.append)
    pipeline.start()
    try:
        # 2 docs separated by a blank page: [p1 p2] X [p4]
        jobs = write_pages(tmp_path, ["text", "text", "blank", "text"])
        pipeline.submit_batch("batch-1", jobs, operator="op1")
        assert wait_for(lambda: len(db.list_documents("batch-1")) == 2 and all(
            d["delivery_status"] == "delivered_local"
            for d in db.list_documents("batch-1")))
    finally:
        pipeline.shutdown()

    docs = db.list_documents("batch-1")
    assert {d["page_range"] for d in docs} == {"1-2", "4-4"}
    assert all(d["validation_status"] == "auto_approved" for d in docs)
    assert all(d["storage_path"] and Path(d["storage_path"]).exists()
               for d in docs)
    delivered = list((tmp_path / "delivered").glob("*.pdf"))
    assert len(delivered) == 2
    # all jobs complete; batch complete; events fired for each stage
    assert not db.get_incomplete_jobs()
    batch = db.conn.execute("SELECT * FROM batches").fetchone()
    assert batch["status"] == "complete"
    stages = {e["stage"] for e in events}
    assert {"ingest", "ocr", "enhance", "qc", "separation",
            "rule", "delivery", "batch"} <= stages


def test_failed_qc_page_held_for_review(tmp_path, db):
    profile = make_profile(tmp_path)
    pipeline = build_pipeline(tmp_path, db, profile)
    events = []
    pipeline.subscribe(events.append)
    pipeline.start()
    try:
        jobs = write_pages(tmp_path, ["text", "blank", "blur"])
        pipeline.submit_batch("batch-1", jobs)
        assert wait_for(lambda: len(db.list_documents("batch-1")) == 2 and any(
            e["stage"] == "delivery" and e["status"] == "held_for_review"
            for e in events))
    finally:
        pipeline.shutdown()

    docs = {d["page_range"]: d for d in db.list_documents("batch-1")}
    good, bad = docs["1-1"], docs["3-3"]
    assert good["validation_status"] == "auto_approved"
    assert good["delivery_status"] == "delivered_local"
    assert bad["validation_status"] == "pending_qc"
    assert bad["delivery_status"] == "pending"
    assert bad["storage_path"] is None  # never exported
    batch = db.conn.execute("SELECT * FROM batches").fetchone()
    assert batch["status"] != "complete"  # review still outstanding


def test_rules_applied_during_pipeline(tmp_path, db):
    rules = {"rule_set_id": "rs", "version": 1, "rules": [{
        "id": "r1", "name": "route all to subfolder",
        "condition": {"field": "qc_score", "op": "gte", "value": 0},
        "actions": [{"type": "set_folder", "value": "Routed"}],
    }]}
    profile = make_profile(tmp_path, rules=rules)
    pipeline = build_pipeline(tmp_path, db, profile)
    pipeline.start()
    try:
        jobs = write_pages(tmp_path, ["text"])
        pipeline.submit_batch("batch-1", jobs)
        assert wait_for(lambda: any(
            d["delivery_status"] == "delivered_local"
            for d in db.list_documents("batch-1")))
    finally:
        pipeline.shutdown()
    routed = list((tmp_path / "delivered" / "Routed").glob("*.pdf"))
    assert len(routed) == 1


def test_crash_recovery_requeues_jobs(tmp_path, db):
    profile = make_profile(tmp_path)
    # simulate a crash: jobs persisted but never processed
    db.insert_project("proj-1", "P", profile)
    db.insert_batch("batch-9", "proj-1", "op")
    jobs = write_pages(tmp_path, ["text", "text"])
    for j in jobs:
        db.upsert_job(j.job_id, "batch-9", "proj-1", j.page_no,
                      "in_progress", j.image_path)

    pipeline = build_pipeline(tmp_path, db, profile)
    recovered = pipeline.recover()
    assert len(recovered) == 2
    pipeline.start()
    try:
        assert wait_for(lambda: not db.get_incomplete_jobs())
    finally:
        pipeline.shutdown()
    assert len(db.list_documents()) >= 1
