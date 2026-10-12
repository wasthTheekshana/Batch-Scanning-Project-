"""Per-batch project runtime in Pipeline (design spec 2026-10-12)."""
from __future__ import annotations

import copy
import logging
import threading

import cv2
import pytest

from core.job_queue import Pipeline
from core.models import ScanJob
from core.project_runtime import ProjectUnavailable, RuntimeResolver
from data.db import Database
from delivery.local_folder import LocalFolderChannel
from export.batch_exporter import export_document
from tests.conftest import make_blank_page, make_text_page
from tests.test_pipeline import wait_for
from tests.test_project_runtime import valid_profile


def make_jobs(tmp_path, batch_id, project_id, kinds):
    jobs = []
    for i, kind in enumerate(kinds, start=1):
        img = make_blank_page() if kind == "blank" else make_text_page()
        p = tmp_path / f"{batch_id}_{i}.png"
        cv2.imwrite(str(p), img)
        jobs.append(ScanJob(batch_id=batch_id, project_id=project_id,
                            page_no=i, image_path=str(p)))
    return jobs


@pytest.fixture()
def env(tmp_path):
    db = Database(tmp_path / "mp.db")
    cfg_a = valid_profile("proj-a", "Alpha", tmp_path / "A_out", tmp_path / "A_deliv",
                          fmt="image_pdf", strategy=("blank_page",))
    cfg_b = valid_profile("proj-b", "Bravo", tmp_path / "B_out", tmp_path / "B_deliv",
                          fmt="tiff", strategy=("fixed_pages",))
    cfg_b["separation"]["fixed_pages"] = 1          # every page its own document
    db.insert_project("proj-a", "Alpha", cfg_a)
    db.insert_project("proj-b", "Bravo", cfg_b)
    pipeline = Pipeline(db, cfg_a, [LocalFolderChannel(cfg_a["delivery"]["local_folder"])],
                        export_document, working_dir=tmp_path / "staging",
                        resolver=RuntimeResolver(db))
    pipeline.start()
    yield type("E", (), dict(db=db, pipeline=pipeline, tmp=tmp_path, a=cfg_a, b=cfg_b))
    pipeline.shutdown()
    db.close()


def delivered(env, which):
    return sorted(p.name for p in (env.tmp / f"{which}_deliv").glob("*"))


def docs_done(env, batch_id, n):
    d = env.db.list_documents(batch_id)
    return len(d) == n and all(x["delivery_status"] == "delivered_local" for x in d)


def test_a_batch_is_filed_and_processed_under_the_project_of_its_jobs(env):
    env.pipeline.submit_batch("batch-b", make_jobs(env.tmp, "batch-b", "proj-b", ["text", "text"]))
    assert wait_for(lambda: docs_done(env, "batch-b", 2))
    assert env.db.get_batch("batch-b")["project_id"] == "proj-b"
    assert {d["project_id"] for d in env.db.list_documents("batch-b")} == {"proj-b"}
    assert delivered(env, "B") == ["batch-b_0001.tif", "batch-b_0002.tif"]   # B's format + folder
    assert delivered(env, "A") == []


def test_two_projects_in_flight_at_once_each_use_their_own_settings(env):
    # A: blank-page separation -> [p1 p2] X [p4] = 2 docs, image_pdf into A_deliv
    # B: fixed 1 page/document -> 3 docs, tiff into B_deliv ; submitted back to back
    env.pipeline.submit_batch("batch-a", make_jobs(env.tmp, "batch-a", "proj-a", ["text", "text", "blank", "text"]))
    env.pipeline.submit_batch("batch-b", make_jobs(env.tmp, "batch-b", "proj-b", ["text", "text", "text"]))
    assert wait_for(lambda: docs_done(env, "batch-a", 2) and docs_done(env, "batch-b", 3), 60)
    assert delivered(env, "A") == ["batch-a_0001.pdf", "batch-a_0002.pdf"]
    assert delivered(env, "B") == ["batch-b_0001.tif", "batch-b_0002.tif", "batch-b_0003.tif"]
    assert {d["project_id"] for d in env.db.list_documents("batch-a")} == {"proj-a"}


def test_edit_between_batches_affects_only_the_second(env):
    env.pipeline.submit_batch("one", make_jobs(env.tmp, "one", "proj-a", ["text"]))
    assert wait_for(lambda: docs_done(env, "one", 1))
    cfg = copy.deepcopy(env.a)
    cfg["export"]["format"] = "tiff"
    env.db.update_project("proj-a", "Alpha", cfg, version=2)
    env.pipeline.submit_batch("two", make_jobs(env.tmp, "two", "proj-a", ["text"]))
    assert wait_for(lambda: docs_done(env, "two", 1))
    assert delivered(env, "A") == ["one_0001.pdf", "two_0001.tif"]    # no restart needed


def test_edit_mid_flight_does_not_change_a_running_batch(env):
    # the batch is parked in the OCR stage, i.e. BEFORE its separation, export
    # and delivery stages have read any setting; the edit lands while it waits
    gate_in, release = threading.Event(), threading.Event()

    def gated_ocr(image):
        gate_in.set()
        assert release.wait(30)
        return None

    env.pipeline.ocr_fn = gated_ocr
    env.pipeline.submit_batch("fly", make_jobs(env.tmp, "fly", "proj-a", ["text"]))
    assert gate_in.wait(30)
    cfg = copy.deepcopy(env.a)
    cfg["export"]["format"] = "tiff"
    env.db.update_project("proj-a", "Alpha", cfg, version=2)
    release.set()
    assert wait_for(lambda: docs_done(env, "fly", 1))
    assert delivered(env, "A") == ["fly_0001.pdf"]       # still the snapshot (image_pdf)


def test_unavailable_project_persists_nothing_and_raises(env):
    jobs = make_jobs(env.tmp, "ghost", "proj-missing", ["text"])
    with pytest.raises(ProjectUnavailable):
        env.pipeline.submit_batch("ghost", jobs)
    assert env.db.get_batch("ghost") is None
    assert env.db.conn.execute("SELECT COUNT(*) FROM jobs WHERE batch_id='ghost'").fetchone()[0] == 0
    assert env.db.get_project("proj-missing") is None


def test_broken_stored_config_blocks_only_that_project(env):
    env.db.conn.execute("UPDATE projects SET config_json='{oops' WHERE project_id='proj-b'")
    env.db.conn.commit()
    with pytest.raises(ProjectUnavailable):
        env.pipeline.submit_batch("bad", make_jobs(env.tmp, "bad", "proj-b", ["text"]))
    env.pipeline.submit_batch("fine", make_jobs(env.tmp, "fine", "proj-a", ["text"]))
    assert wait_for(lambda: docs_done(env, "fine", 1))


def test_mixed_project_ids_in_one_batch_are_refused(env):
    jobs = make_jobs(env.tmp, "mix", "proj-a", ["text"]) + make_jobs(env.tmp, "mix", "proj-b", ["text"])
    with pytest.raises(ValueError, match="one project"):
        env.pipeline.submit_batch("mix", jobs)
    assert env.db.get_batch("mix") is None


def test_documents_are_inserted_with_the_batchs_project_not_the_default(env):
    env.pipeline.submit_batch("meta", make_jobs(env.tmp, "meta", "proj-b", ["text"]))
    assert wait_for(lambda: docs_done(env, "meta", 1))
    assert env.db.list_documents("meta")[0]["project_id"] == "proj-b"      # pipeline default is proj-a


def test_project_unavailable_in_a_stage_logs_the_message_but_never_the_cause(env, caplog):
    secret = "S3CRET-VALUE-123"

    def unavailable(batch_id, project_id=None):
        raise ProjectUnavailable("project 'proj-a': stored config is invalid") \
            from ValueError(f"bad value {secret}")

    events = []
    env.pipeline.subscribe(events.append)
    env.pipeline._rt = unavailable
    with caplog.at_level(logging.ERROR, logger="core.job_queue"):
        env.pipeline.submit_batch("susp", make_jobs(env.tmp, "susp", "proj-a", ["text"]))
        assert wait_for(lambda: any(e["status"] == "failed" for e in events))
    assert "stage ingest failed: project 'proj-a': stored config is invalid" in caplog.text
    assert secret not in caplog.text
    assert "Traceback" not in caplog.text
    assert not any(r.exc_info for r in caplog.records)
    failed = [e for e in events if e["status"] == "failed"][0]
    assert secret not in failed["error"]


def test_without_a_resolver_everything_is_as_before(tmp_path):
    db = Database(tmp_path / "legacy.db")
    cfg = valid_profile("proj-a", "Alpha", tmp_path / "out", tmp_path / "deliv")
    p = Pipeline(db, cfg, [LocalFolderChannel(cfg["delivery"]["local_folder"])],
                 export_document, working_dir=tmp_path / "st")
    p.start()
    try:
        p.submit_batch("legacy", make_jobs(tmp_path, "legacy", "proj-a", ["text"]))
        assert wait_for(lambda: len(db.list_documents("legacy")) == 1 and
                        db.list_documents("legacy")[0]["delivery_status"] == "delivered_local")
        assert db.get_project("proj-a") is not None        # default project row auto-created
    finally:
        p.shutdown()
        db.close()
