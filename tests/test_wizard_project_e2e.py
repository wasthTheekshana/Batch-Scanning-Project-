"""End to end: a project picked in the scan wizard drives the REAL pipeline.

A real MainWindow with a real Pipeline(resolver=RuntimeResolver(db)); the
wizard's hand-off goes through MainWindow.start_batch. Two projects with
different export formats and delivery folders: a batch for the second one is
exported, delivered and filed under the second one, and an edit made between
two batches reaches the next batch without restarting anything."""
import copy

import cv2
import pytest

from core.event_bus import EventBus
from core.job_queue import Pipeline
from core.profile_manager import ProfileManager
from core.project_runtime import RuntimeResolver
from core.rbac import Session, User
from data.audit import AuditLogger
from data.db import Database
from delivery.local_folder import LocalFolderChannel
from export.batch_exporter import export_document
from tests.conftest import make_text_page
from tests.test_pipeline import wait_for
from tests.test_project_runtime import valid_profile
from ui.main_window import MainWindow
from ui.scan_wizard import ScanWizard


def make_env(tmp_path, qtbot, resolver_factory):
    db = Database(tmp_path / "e2e.db")
    pm = ProfileManager(tmp_path / "projects", db)
    cfg_a = pm.adopt(valid_profile("proj-a", "Alpha", tmp_path / "A_out",
                                   tmp_path / "A_deliv", fmt="image_pdf"))
    cfg_b = pm.adopt(valid_profile("proj-b", "Bravo", tmp_path / "B_out",
                                   tmp_path / "B_deliv", fmt="tiff"))
    audit = AuditLogger(db, tmp_path / "logs")
    pipeline = Pipeline(
        db, cfg_a, [LocalFolderChannel(cfg_a["delivery"]["local_folder"])],
        export_document, working_dir=tmp_path / "pipe-staging",
        resolver=resolver_factory(db))
    pipeline.start()
    Session._current = User("a1", "root", "admin")
    win = MainWindow(db, pm, EventBus(), pipeline=pipeline, audit=audit,
                     staging_dir=tmp_path / "staging")
    qtbot.addWidget(win)
    return db, pm, audit, pipeline, win, cfg_b


@pytest.fixture()
def make(tmp_path, qtbot):
    made = []

    def build(resolver_factory=RuntimeResolver):
        env = make_env(tmp_path, qtbot, resolver_factory)
        made.append(env)
        return env

    yield build
    for db, _pm, audit, pipeline, _win, _cfg in made:
        pipeline.shutdown()
        Session.logout()
        audit.close()
        db.close()


def scan_via_wizard(qtbot, win, db, tmp_path, project_id, name):
    """Drive the wizard like the operator: pick the project, add a file."""
    img = tmp_path / f"{name}.png"
    cv2.imwrite(str(img), make_text_page())
    projects = [{"project_id": p["project_id"], "name": p["name"],
                 "config": {}} for p in db.list_projects()]
    wizard = ScanWizard(projects, tmp_path / f"wiz-{name}")
    qtbot.addWidget(wizard)
    batches = []
    wizard.batch_ready.connect(lambda b, jobs: batches.append(b))
    wizard.batch_ready.connect(win.start_batch)
    wizard.project_page.combo.setCurrentIndex(
        next(i for i, p in enumerate(projects)
             if p["project_id"] == project_id))
    wizard.source_page.file_list.addItem(str(img))
    wizard.accept()
    assert len(batches) == 1
    return batches[0]


def delivered(tmp_path, which):
    return sorted(p.suffix for p in (tmp_path / f"{which}_deliv").glob("*"))


def done(db, batch_id):
    docs = db.list_documents(batch_id)
    return len(docs) == 1 and docs[0]["delivery_status"] == "delivered_local"


def test_second_project_chosen_in_the_wizard_is_delivered_as_that_project(
        qtbot, make, tmp_path):
    env = make()
    db, win = env[0], env[4]
    batch = scan_via_wizard(qtbot, win, db, tmp_path, "proj-b", "one")
    assert wait_for(lambda: done(db, batch))
    assert delivered(tmp_path, "B") == [".tif"]        # Bravo's folder + format
    assert delivered(tmp_path, "A") == []              # Alpha's folder untouched
    assert db.get_batch(batch)["project_id"] == "proj-b"
    assert {d["project_id"] for d in db.list_documents(batch)} == {"proj-b"}


def test_edit_between_two_wizard_batches_applies_without_restart(
        qtbot, make, tmp_path):
    db, pm, _a, _p, win, cfg_b = make()
    first = scan_via_wizard(qtbot, win, db, tmp_path, "proj-b", "one")
    assert wait_for(lambda: done(db, first))
    edited = copy.deepcopy(cfg_b)
    edited["export"]["format"] = "image_pdf"           # Designer / central edit
    pm.save(edited)
    second = scan_via_wizard(qtbot, win, db, tmp_path, "proj-b", "two")
    assert wait_for(lambda: done(db, second))
    assert delivered(tmp_path, "B") == [".pdf", ".tif"]   # old batch kept, new one NEW format
    assert db.get_batch(second)["project_id"] == "proj-b"
