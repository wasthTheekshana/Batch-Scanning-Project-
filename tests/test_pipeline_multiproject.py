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
    try:
        env.pipeline.submit_batch("fly", make_jobs(env.tmp, "fly", "proj-a", ["text"]))
        assert gate_in.wait(30)
        cfg = copy.deepcopy(env.a)
        cfg["export"]["format"] = "tiff"
        env.db.update_project("proj-a", "Alpha", cfg, version=2)
    finally:
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
    assert {r["status"] for r in env.db.conn.execute(
        "SELECT status FROM jobs WHERE batch_id='susp'")} == {"failed"}
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


def _persist_unprocessed(env, batch, batch_row_pid, job_pid):
    """Simulate a crash: batch row + persisted-but-unprocessed jobs."""
    env.db.insert_batch(batch, batch_row_pid, "op")
    for j in make_jobs(env.tmp, batch, job_pid, ["text"]):
        env.db.upsert_job(j.job_id, j.batch_id, j.project_id, j.page_no,
                          "in_progress", j.image_path)


def test_recovery_uses_each_batchs_own_project(env):
    _persist_unprocessed(env, "rec-a", "proj-a", "proj-a")
    _persist_unprocessed(env, "rec-b", "proj-b", "proj-b")
    recovered = env.pipeline.recover()
    assert len(recovered) == 2
    assert wait_for(lambda: docs_done(env, "rec-a", 1) and docs_done(env, "rec-b", 1), 60)
    assert delivered(env, "A") == ["rec-a_0001.pdf"]
    assert delivered(env, "B") == ["rec-b_0001.tif"]


def test_recovered_batch_is_processed_entirely_under_its_jobs_project(env):
    # legacy data: the batch row says proj-a, its jobs say proj-b
    _persist_unprocessed(env, "rec-mixed", "proj-a", "proj-b")
    env.pipeline.recover()
    assert wait_for(lambda: docs_done(env, "rec-mixed", 1), 60)
    assert {d["project_id"] for d in env.db.list_documents("rec-mixed")} == {"proj-b"}
    assert delivered(env, "B") == ["rec-mixed_0001.tif"]
    assert delivered(env, "A") == []


def test_recovery_fails_a_batch_whose_project_is_unavailable_and_continues(env, caplog):
    secret = "S3CRET-VALUE-123"
    bad = copy.deepcopy(env.a)
    bad["project_id"] = "proj-gone"
    bad["export"]["format"] = secret                    # schema-invalid enum value
    # batches.project_id is a foreign key: model "broken stored config", not "no row"
    env.db.insert_project("proj-gone", "Gone", bad)
    _persist_unprocessed(env, "rec-x", "proj-gone", "proj-gone")
    _persist_unprocessed(env, "rec-a", "proj-a", "proj-a")
    events = []
    env.pipeline.subscribe(events.append)
    with caplog.at_level(logging.INFO, logger="core.job_queue"):
        recovered = env.pipeline.recover()                  # must not raise
    assert [j.batch_id for j in recovered] == ["rec-a"]
    assert wait_for(lambda: docs_done(env, "rec-a", 1))
    statuses = {r["status"] for r in env.db.conn.execute(
        "SELECT status FROM jobs WHERE batch_id='rec-x'")}
    assert statuses == {"failed"}
    assert any(e["stage"] == "recover" and e["status"] == "failed"
               and e.get("batch_id") == "rec-x" for e in events)
    assert "proj-gone" in caplog.text
    assert secret not in caplog.text
    assert not any(r.exc_info for r in caplog.records)
    assert all(secret not in str(e) for e in events)


def _held_document(env, batch, pid):
    """A pending_qc document whose pages exist on disk, ready for review approval."""
    pages = make_jobs(env.tmp, batch, pid, ["text"])
    env.db.insert_batch(batch, pid, "op")
    env.db.insert_document(f"doc-{batch}", batch, pid, page_range="1-1",
                           enhancement_profile="default", extracted_fields={},
                           validation_status="pending_qc", qc_score=50,
                           qc_detail={"pages": {"1": {"working_path": pages[0].image_path,
                                                      "original_path": pages[0].image_path,
                                                      "ocr_text": ""}}},
                           duplicate_of=None)
    return f"doc-{batch}"


def test_review_approved_document_uses_its_own_projects_settings(env):
    doc_id = _held_document(env, "held-b", "proj-b")          # pipeline default is proj-a
    assert env.pipeline.resubmit_document(doc_id) is True
    assert wait_for(lambda: delivered(env, "B") == ["held-b_0001.tif"])
    assert delivered(env, "A") == []


def test_review_approved_document_in_a_live_batch_uses_the_batchs_snapshot(env):
    doc_id = _held_document(env, "held-s", "proj-a")
    env.pipeline._batches["held-s"] = {"expected": 1, "pages": [], "counter": 0,
                                       "operator": "op",
                                       "runtime": env.pipeline._resolve("proj-a")}
    cfg = copy.deepcopy(env.a)
    cfg["export"]["format"] = "tiff"
    env.db.update_project("proj-a", "Alpha", cfg, version=2)   # edit after the snapshot
    assert env.pipeline.resubmit_document(doc_id) is True
    assert wait_for(lambda: delivered(env, "A") == ["held-s_0001.pdf"])


def test_resubmit_returns_false_when_the_documents_project_is_unavailable(env, caplog):
    env.db.insert_project("proj-gone", "Gone", {"project_id": "proj-gone"})   # FK target, invalid config
    doc_id = _held_document(env, "held-x", "proj-gone")
    with caplog.at_level(logging.WARNING, logger="core.job_queue"):
        assert env.pipeline.resubmit_document(doc_id) is False
    assert "proj-gone" in caplog.text
    assert delivered(env, "A") == [] and delivered(env, "B") == []


# -- final-review fixes: recovery visibility, legacy mode, atomic submit -----------

def _persist_two_project_batch(env, batch):
    """A crashed batch whose jobs carry two different project ids."""
    env.db.insert_batch(batch, "proj-a", "op")
    for pid in ("proj-a", "proj-b"):
        for j in make_jobs(env.tmp, f"{batch}-{pid}", pid, ["text"]):
            env.db.upsert_job(j.job_id, batch, pid, 1 if pid == "proj-a" else 2,
                              "in_progress", j.image_path)


def _broken_project(env, project_id="proj-gone", secret="S3CRET-VALUE-123"):
    bad = copy.deepcopy(env.a)
    bad["project_id"] = project_id
    bad["export"]["format"] = secret                    # schema-invalid enum value
    env.db.insert_project(project_id, "Gone", bad)
    return secret


def test_failed_recovery_marks_the_batch_row_failed(env):
    _broken_project(env)
    _persist_unprocessed(env, "rec-x", "proj-gone", "proj-gone")
    env.pipeline.recover()
    assert env.db.get_batch("rec-x")["status"] == "failed"


def test_refused_recovery_is_recorded_value_free_and_reset_per_call(env):
    secret = _broken_project(env)
    _persist_unprocessed(env, "rec-x", "proj-gone", "proj-gone")
    _persist_unprocessed(env, "rec-a", "proj-a", "proj-a")
    env.pipeline.recover()
    refused = env.pipeline.refused_batches
    assert [b for b, _m in refused] == ["rec-x"]
    assert "proj-gone" in refused[0][1]
    assert secret not in refused[0][1]
    assert wait_for(lambda: docs_done(env, "rec-a", 1))
    env.pipeline.recover()                      # nothing left to recover
    assert env.pipeline.refused_batches == []


def test_recovery_with_a_resolver_fails_a_batch_spanning_several_projects(env):
    _persist_two_project_batch(env, "rec-mix")
    _persist_unprocessed(env, "rec-a", "proj-a", "proj-a")
    events = []
    env.pipeline.subscribe(events.append)
    recovered = env.pipeline.recover()
    assert [j.batch_id for j in recovered] == ["rec-a"]
    assert {r["status"] for r in env.db.conn.execute(
        "SELECT status FROM jobs WHERE batch_id='rec-mix'")} == {"failed"}
    assert any(e["stage"] == "recover" and e["status"] == "failed"
               and e.get("batch_id") == "rec-mix" for e in events)
    assert env.db.get_batch("rec-mix")["status"] == "failed"
    assert env.pipeline.refused_batches == [
        ("rec-mix", "recovered batch spans several projects")]
    assert wait_for(lambda: docs_done(env, "rec-a", 1))


def test_recovery_without_a_resolver_ignores_the_projects_of_the_jobs(env):
    _persist_two_project_batch(env, "rec-mix")
    legacy = Pipeline(env.db, env.a,
                      [LocalFolderChannel(env.a["delivery"]["local_folder"])],
                      export_document, working_dir=env.tmp / "legacy-staging")
    events = []
    legacy.subscribe(events.append)
    recovered = legacy.recover()                # workers not started: jobs just queue
    assert len(recovered) == 2
    assert legacy.ingest_q.qsize() == 2
    assert not any(e["status"] == "failed" for e in events)
    assert legacy.refused_batches == []
    assert {r["status"] for r in env.db.conn.execute(
        "SELECT status FROM jobs WHERE batch_id='rec-mix'")} == {"queued"}
    assert env.db.get_batch("rec-mix")["status"] == "in_progress"


def test_a_db_error_in_submit_leaves_no_in_memory_batch_and_enqueues_nothing(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db is down")

    monkeypatch.setattr(env.db, "insert_batch", boom)
    with pytest.raises(RuntimeError):
        env.pipeline.submit_batch("nope", make_jobs(env.tmp, "nope", "proj-a", ["text"]))
    assert "nope" not in env.pipeline._batches
    assert env.pipeline.ingest_q.empty()
    assert env.db.conn.execute("SELECT COUNT(*) FROM jobs WHERE batch_id='nope'").fetchone()[0] == 0


# -- can_resubmit ----------------------------------------------------------------

def test_can_resubmit_reports_the_reason_without_side_effects(env):
    ok_id = _held_document(env, "cr-ok", "proj-a")
    assert env.pipeline.can_resubmit(ok_id) == (True, "")
    assert env.pipeline.can_resubmit("no-such-doc") == (False, "unknown document")

    env.db.insert_project("proj-gone", "Gone", {"project_id": "proj-gone"})
    gone_id = _held_document(env, "cr-gone", "proj-gone")
    ok, reason = env.pipeline.can_resubmit(gone_id)
    assert ok is False
    assert reason.startswith("project not available: ")
    assert "proj-gone" in reason

    nopages = _held_document(env, "cr-nopages", "proj-a")
    env.db.update_document(nopages, qc_detail={"pages": {}})
    assert env.pipeline.can_resubmit(nopages) == (False, "page images unavailable")
    assert env.pipeline.delivery_q.empty()


# -- M2: lookup validation is scoped to the project ---------------------------------

def _doc_with_field(env, n, pid, value):
    batch = f"lk-{pid}-{n}"
    env.db.insert_batch(batch, pid, "op")
    env.db.insert_document(f"doc-{batch}", batch, pid, page_range="1-1",
                           enhancement_profile="default",
                           extracted_fields={"invoice": value},
                           validation_status="auto_approved", qc_score=100,
                           qc_detail={}, duplicate_of=None)


def test_lookup_history_of_one_project_does_not_gate_another(env):
    for i in range(6):
        _doc_with_field(env, i, "proj-a", f"A-{i}")
    assert env.pipeline._fails_lookup("invoice", "B-new", "proj-a") is True
    assert env.pipeline._fails_lookup("invoice", "B-new", "proj-b") is False   # B has no history yet
    for i in range(5):
        _doc_with_field(env, i, "proj-b", f"B-{i}")
    assert env.pipeline._fails_lookup("invoice", "B-3", "proj-b") is False     # known in B
    assert env.pipeline._fails_lookup("invoice", "A-3", "proj-b") is True      # A's value unknown to B
    assert env.pipeline._fails_lookup("invoice", "B-new", "proj-b") is True


def test_known_field_values_default_is_still_across_all_projects(env):
    _doc_with_field(env, 0, "proj-a", "A-0")
    _doc_with_field(env, 0, "proj-b", "B-0")
    assert env.db.known_field_values("invoice") == {"A-0", "B-0"}
    assert env.db.known_field_values("invoice", project_id="proj-b") == {"B-0"}
