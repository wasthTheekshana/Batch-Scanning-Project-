"""A network scanner drops files in a folder: each becomes a batch."""
import json

import cv2
import pytest

from core.hot_folder import OPERATOR, HotFolderService
from core.project_runtime import ProjectUnavailable
from data.db import Database
from tests.conftest import make_text_page


class FakePipeline:
    def __init__(self):
        self.batches = []
        self.fail = None

    def submit_batch(self, batch_id, jobs, operator="system"):
        if self.fail:
            raise self.fail
        self.batches.append((batch_id, jobs, operator))


class Audit:
    def __init__(self):
        self.rows = []

    def log(self, action, user, **kw):
        self.rows.append((action, user, kw))


@pytest.fixture()
def env(tmp_path):
    db = Database(tmp_path / "hf.db")
    watch = tmp_path / "inbox"
    watch.mkdir()
    cfg = {"scanner": {"dpi": 300, "hot_folder": {"enabled": True, "path": str(watch)}}}
    db.insert_project("p1", "Bank", cfg)
    pipe, audit = FakePipeline(), Audit()
    svc = HotFolderService(db, pipe, tmp_path / "staging", audit=audit, poll_seconds=0.05)
    yield svc, pipe, audit, watch, db
    svc.stop()
    db.close()


def drop(folder, name="scan.png"):
    path = folder / name
    cv2.imwrite(str(path), make_text_page())
    return path


def test_only_active_projects_with_the_folder_switched_on_are_watched(env, tmp_path):
    svc, _p, _a, watch, db = env
    off = tmp_path / "off"
    db.insert_project("p2", "Off", {"scanner": {"hot_folder": {"enabled": False, "path": str(off)}}})
    db.insert_project("p3", "None", {"scanner": {"dpi": 300}})
    db.insert_project("p4", "Blank", {"scanner": {"hot_folder": {"enabled": True, "path": " "}}})
    db.insert_project("p5", "Dup", {"scanner": {"hot_folder": {"enabled": True, "path": str(watch)}}})
    assert svc.wanted() == {watch: "p1"}              # a shared folder goes to the first


def test_a_dropped_file_becomes_a_batch_and_moves_to_processed(env):
    svc, pipe, audit, watch, _db = env
    path = drop(watch)
    batch_id = svc.handle("p1", path)
    assert batch_id and not path.exists() and (watch / "processed" / "scan.png").exists()
    (bid, jobs, operator), = pipe.batches
    assert bid == batch_id and operator == OPERATOR and len(jobs) == 1
    assert jobs[0].project_id == "p1"
    assert audit.rows[0][0] == "hot_folder_batch" and audit.rows[0][2]["pages"] == 1


def test_an_unreadable_file_goes_to_failed_and_nothing_is_submitted(env):
    svc, pipe, audit, watch, _db = env
    bad = watch / "broken.pdf"
    bad.write_bytes(b"not a pdf")
    assert svc.handle("p1", bad) is None
    assert (watch / "failed" / "broken.pdf").exists() and not pipe.batches
    assert audit.rows[0][0] == "hot_folder_failed"


def test_an_unavailable_project_sends_the_file_to_failed(env):
    svc, pipe, _a, watch, _db = env
    pipe.fail = ProjectUnavailable("p1")
    assert svc.handle("p1", drop(watch)) is None
    assert (watch / "failed" / "scan.png").exists()


def test_a_second_file_with_the_same_name_is_not_overwritten(env):
    svc, _p, _a, watch, _db = env
    svc.handle("p1", drop(watch))
    svc.handle("p1", drop(watch))
    assert len(list((watch / "processed").iterdir())) == 2


def test_files_already_waiting_when_the_app_starts_are_picked_up(env):
    svc, pipe, _a, watch, _db = env
    drop(watch)                                      # arrived while the app was closed
    svc.reload()
    watcher = next(iter(svc._watchers.values()))
    watcher.poll_once()                              # size seen
    watcher.poll_once()                              # size stable: dispatched
    assert len(pipe.batches) == 1 and (watch / "processed" / "scan.png").exists()


def test_reload_follows_project_changes(env, tmp_path):
    svc, _p, _a, watch, db = env
    svc.reload()
    assert set(svc._watchers) == {watch}
    db.update_project("p1", "Bank", {"scanner": {"dpi": 300}}, 2)
    svc.reload()
    assert svc._watchers == {}


def test_subfolders_are_not_watched(env):
    svc, pipe, _a, watch, _db = env
    (watch / "processed").mkdir()
    drop(watch / "processed", "old.png")
    svc.reload()
    watcher = next(iter(svc._watchers.values()))
    watcher.poll_once(); watcher.poll_once()
    assert not pipe.batches


def test_the_schema_accepts_the_setting():
    schema = json.loads(open("core/project_schema.json", encoding="utf-8").read())
    props = schema["properties"]["scanner"]["properties"]["hot_folder"]["properties"]
    assert set(props) == {"enabled", "path"}


# -- the Designer ---------------------------------------------------------------------
def test_the_designer_saves_and_reloads_the_hot_folder_setting(qtbot, tmp_path):
    from core.profile_manager import ProfileManager
    from core.rbac import User
    from ui.workflow_designer import WorkflowDesigner
    db = Database(tmp_path / "d.db")
    pm = ProfileManager(tmp_path / "projects", db)
    pm.save(pm.create("P"))
    d = WorkflowDesigner(db, pm, User("u1", "root", "admin"))
    qtbot.addWidget(d)
    assert not d.hot_enabled.isChecked() and "hot_folder" not in d.collect_config()["scanner"]
    d.hot_enabled.setChecked(True)
    d.hot_path.setText(str(tmp_path / "inbox"))
    assert d.collect_config()["scanner"]["hot_folder"] == {
        "enabled": True, "path": str(tmp_path / "inbox")}
    d.hot_enabled.setChecked(False)
    d.hot_path.setText("")
    assert "hot_folder" not in d.collect_config()["scanner"]
    db.close()


def test_a_dropped_file_runs_through_the_real_pipeline(tmp_path):
    from tests.test_pipeline import build_pipeline, make_profile, wait_for
    db = Database(tmp_path / "e2e.db")
    profile = make_profile(tmp_path)
    profile["separation"] = {"strategy": ["fixed_pages"], "fixed_pages": 1,
                             "blank_threshold": 0.02}
    db.insert_project(profile["project_id"], profile["name"], profile)
    pipeline = build_pipeline(tmp_path, db, profile)
    watch = tmp_path / "inbox"
    watch.mkdir()
    cfg = {**profile, "scanner": {**profile["scanner"],
                                  "hot_folder": {"enabled": True, "path": str(watch)}}}
    db.update_project(profile["project_id"], profile["name"], cfg, 2)
    svc = HotFolderService(db, pipeline, tmp_path / "staging", poll_seconds=0.05)
    pipeline.start()
    try:
        batch_id = svc.handle(profile["project_id"], drop(watch))
        assert batch_id
        assert wait_for(lambda: len(db.list_documents(batch_id)) == 1)
        assert db.get_batch(batch_id)["operator_user"] == OPERATOR
    finally:
        pipeline.shutdown()
        db.close()


# -- hostile or broken input ---------------------------------------------------------------
def test_a_symlink_in_the_folder_is_never_read(env, tmp_path):
    svc, pipe, _a, watch, _db = env
    secret = drop(tmp_path, "secret.png")                # outside the hot folder
    link = watch / "link.png"
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("symlinks need privileges on this PC")
    svc.reload()
    watcher = next(iter(svc._watchers.values()))
    watcher.poll_once(); watcher.poll_once()
    assert not pipe.batches and secret.exists()          # ignored, and the target untouched
    assert svc.handle("p1", link) is None                # even when handed over directly
    assert not pipe.batches and secret.exists()


def test_an_oversize_file_is_refused_unread(env, monkeypatch):
    import core.hot_folder as hf
    svc, pipe, _a, watch, _db = env
    monkeypatch.setattr(hf, "MAX_FILE_BYTES", 10)
    assert svc.handle("p1", drop(watch)) is None
    assert (watch / "failed" / "scan.png").exists() and not pipe.batches


def test_pages_staged_for_a_refused_batch_are_removed(env):
    svc, pipe, _a, watch, _db = env
    pipe.fail = ValueError("no")
    svc.handle("p1", drop(watch))
    assert not list(svc.staging_dir.glob("*.png"))
