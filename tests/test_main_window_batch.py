"""MainWindow.start_batch: refused batches show a clear message and record
nothing; accepted batches reach the pipeline with the wizard's project."""
import json
import logging

import cv2
import pytest

from core.event_bus import EventBus
from core.profile_manager import ProfileManager
from core.project_runtime import ProjectUnavailable
from core.rbac import Session, User
from data.audit import AuditLogger
from data.db import Database
from core.models import ScanJob
from tests.conftest import make_text_page
from ui.main_window import MainWindow
from ui.scan_wizard import ScanWizard

SENTINEL = "SECRET-CONFIG-VALUE-9f3a"


class FakePipeline:
    def __init__(self, exc=None):
        self.exc = exc
        self.submitted = []

    def submit_batch(self, batch_id, jobs, operator="system"):
        if self.exc is not None:
            raise self.exc
        self.submitted.append((batch_id, [j.project_id for j in jobs],
                               operator))


@pytest.fixture()
def env(tmp_path):
    db = Database(tmp_path / "w.db")
    pm = ProfileManager(tmp_path / "projects", db)
    audit = AuditLogger(db, tmp_path / "logs")
    yield db, pm, audit, tmp_path
    Session.logout()
    audit.close()
    db.close()


@pytest.fixture()
def window_factory(qtbot, env):
    db, pm, audit, tmp_path = env

    def make(pipeline):
        pm.create("Default Project")
        Session._current = User("a1", "root", "admin")
        win = MainWindow(db, pm, EventBus(), pipeline=pipeline, audit=audit,
                         staging_dir=tmp_path / "staging")
        qtbot.addWidget(win)
        return win, pipeline, audit

    return make


def job(project_id, image_path="page.png"):
    return ScanJob(batch_id="b", project_id=project_id, page_no=1,
                   image_path=image_path, source="file")


def audit_actions(win):
    return [r["action"] for r in win.db.conn.execute(
        "SELECT action FROM audit_log ORDER BY log_id")]


def viewer_page_count(win):
    return win.viewer.thumbnails.count()


def test_start_batch_submits_the_jobs_of_the_chosen_project(
        qtbot, window_factory, tmp_path):
    img = tmp_path / "ok.png"
    cv2.imwrite(str(img), make_text_page())
    win, pipe, _ = window_factory(FakePipeline())
    win.start_batch("b1", [job("proj-b", str(img))])
    assert pipe.submitted == [("b1", ["proj-b"], "root")]
    assert audit_actions(win).count("scan") == 1
    assert viewer_page_count(win) == 1


@pytest.mark.parametrize("exc", [ProjectUnavailable("project 'x' not found"),
                                 ValueError("batch has no jobs")],
                         ids=["unavailable", "value_error"])
def test_refused_batch_shows_a_message_and_records_nothing(
        qtbot, window_factory, monkeypatch, exc):
    shown = []
    monkeypatch.setattr(
        "ui.main_window.QMessageBox.critical",
        staticmethod(lambda *a, **k: shown.append((a[1], a[2]))))
    win, _, _ = window_factory(FakePipeline(exc))
    win.start_batch("b2", [job("x")])
    assert len(shown) == 1
    title, text = shown[0]
    assert str(exc) in text
    if isinstance(exc, ProjectUnavailable):
        assert title == "Project not available"
        assert "Workflow Designer" in text
    else:
        assert title == "Batch not started"
        assert text.startswith("This batch could not be started: batch has no jobs")
        assert "Workflow Designer" not in text
    assert "scan" not in audit_actions(win)
    assert viewer_page_count(win) == 0


def test_refusal_message_and_log_never_carry_the_chained_cause(
        qtbot, window_factory, monkeypatch, caplog):
    shown = []
    monkeypatch.setattr(
        "ui.main_window.QMessageBox.critical",
        staticmethod(lambda *a, **k: shown.append(a[2])))
    try:
        try:
            raise RuntimeError(f"bad config {SENTINEL}")
        except RuntimeError as cause:
            raise ProjectUnavailable("project 'x' is unusable") from cause
    except ProjectUnavailable as exc:
        refusal = exc
    win, _, _ = window_factory(FakePipeline(refusal))
    with caplog.at_level(logging.DEBUG):
        win.start_batch("b3", [job("x")])
    assert shown and "project 'x' is unusable" in shown[0]
    assert "project 'x' is unusable" in caplog.text       # it did log
    assert SENTINEL not in shown[0]
    assert SENTINEL not in caplog.text
    assert "Traceback" not in caplog.text


def test_wizard_choice_reaches_the_pipeline(qtbot, window_factory, tmp_path):
    img = tmp_path / "scan.png"
    cv2.imwrite(str(img), make_text_page())
    win, pipe, _ = window_factory(FakePipeline())
    projects = [{"project_id": "proj-a", "name": "A", "config": {}},
                {"project_id": "proj-b", "name": "B", "config": {}}]
    wizard = ScanWizard(projects, tmp_path / "wiz-staging")
    qtbot.addWidget(wizard)
    wizard.batch_ready.connect(win.start_batch)
    wizard.project_page.combo.setCurrentIndex(1)
    wizard.source_page.file_list.addItem(str(img))
    wizard.accept()
    assert len(pipe.submitted) == 1
    batch_id, project_ids, operator = pipe.submitted[0]
    assert project_ids == ["proj-b"]
    assert operator == "root"
    assert audit_actions(win).count("scan") == 1
    assert json.loads(win.db.conn.execute(
        "SELECT detail FROM audit_log WHERE action='scan'"
    ).fetchone()["detail"])["batch_id"] == batch_id


def _capture_warning(monkeypatch):
    shown = []
    monkeypatch.setattr(
        "ui.main_window.QMessageBox.warning",
        staticmethod(lambda *a, **k: shown.append((a[1], a[2]))))
    return shown


def test_report_refused_batches_lists_each_batch_in_one_box(
        qtbot, window_factory, monkeypatch):
    shown = _capture_warning(monkeypatch)
    win, _, _ = window_factory(FakePipeline())
    win.report_refused_batches([("b-1", "project 'x' not found"),
                                ("b-2", "recovered batch spans several projects")])
    assert len(shown) == 1
    title, text = shown[0]
    assert title == "Batches not recovered"
    assert "b-1: project 'x' not found" in text
    assert "b-2: recovered batch spans several projects" in text
    assert "were not resumed" in text and "rescan" in text


def test_report_refused_batches_shows_nothing_for_an_empty_list(
        qtbot, window_factory, monkeypatch):
    shown = _capture_warning(monkeypatch)
    win, _, _ = window_factory(FakePipeline())
    win.report_refused_batches([])
    assert shown == []


def test_report_refused_batches_caps_a_long_list(
        qtbot, window_factory, monkeypatch):
    shown = _capture_warning(monkeypatch)
    win, _, _ = window_factory(FakePipeline())
    win.report_refused_batches([(f"batch-{i:02d}", "gone") for i in range(13)])
    text = shown[0][1]
    assert "batch-09: gone" in text
    assert "batch-10" not in text
    assert "and 3 more" in text


def test_a_secret_in_a_broken_config_never_reaches_the_recovery_box(
        qtbot, window_factory, monkeypatch, tmp_path):
    """End to end: broken stored config -> real Pipeline.recover() -> box."""
    from core.job_queue import Pipeline
    from core.project_runtime import RuntimeResolver
    from export.batch_exporter import export_document
    from tests.test_project_runtime import valid_profile
    shown = _capture_warning(monkeypatch)
    win, _, _ = window_factory(FakePipeline())
    bad = valid_profile("proj-gone", "Gone", tmp_path / "o", tmp_path / "d")
    bad["export"]["format"] = SENTINEL                  # schema-invalid enum value
    win.db.insert_project("proj-gone", "Gone", bad)
    win.db.insert_batch("rec-x", "proj-gone", "op")
    win.db.upsert_job("j1", "rec-x", "proj-gone", 1, "in_progress", "p.png")
    pipe = Pipeline(win.db, bad, [], export_document,
                    working_dir=tmp_path / "st", resolver=RuntimeResolver(win.db))
    pipe.recover()
    win.report_refused_batches(pipe.refused_batches)
    assert len(shown) == 1
    assert "rec-x: project 'proj-gone'" in shown[0][1]
    assert SENTINEL not in shown[0][1]
