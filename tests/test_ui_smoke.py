"""UI smoke tests — offscreen Qt via pytest-qt."""
import cv2
import pytest

from core.event_bus import EventBus
from core.profile_manager import ProfileManager
from core.rbac import Session, User, hash_password
from data.db import Database
from tests.conftest import make_text_page
from ui.image_viewer import ImageViewer
from ui.job_monitor import JobMonitor
from ui.login_dialog import LoginDialog
from ui.main_window import MainWindow
from ui.qc_review import QCReviewPanel


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "ui.db")
    yield d
    Session.logout()
    d.close()


def login_as(db, role):
    db.insert_user(f"u-{role}", f"{role}1", hash_password("pw"), role)
    return Session.login(f"{role}1", "pw", db)


def test_login_dialog_rejects_bad_credentials(qtbot, db):
    dlg = LoginDialog(db)
    qtbot.addWidget(dlg)
    dlg.username.setText("admin")
    dlg.password.setText("wrong")
    dlg.try_login()
    assert dlg.user is None
    assert dlg.error_label.text() != ""


def test_login_dialog_accepts_default_admin(qtbot, db):
    dlg = LoginDialog(db)
    qtbot.addWidget(dlg)
    dlg.username.setText("admin")
    dlg.password.setText("admin")
    dlg.try_login()
    assert dlg.user is not None and dlg.user.role == "admin"


def test_image_viewer_loads_pages(qtbot, tmp_path):
    paths = []
    for i in range(2):
        p = tmp_path / f"{i}.png"
        cv2.imwrite(str(p), make_text_page())
        paths.append(str(p))
    viewer = ImageViewer()
    qtbot.addWidget(viewer)
    viewer.load_pages(paths)
    assert viewer.thumbnails.count() == 2


def test_job_monitor_shows_events(qtbot):
    bus = EventBus()
    monitor = JobMonitor(bus)
    qtbot.addWidget(monitor)
    bus.publish_pipeline_event({"stage": "qc", "status": "done",
                                "job_id": "j1", "score": 88})
    assert monitor.table.rowCount() == 1
    assert monitor.table.item(0, 1).text() == "qc"


def test_qc_review_lists_pending_documents(qtbot, db):
    user = login_as(db, "qc_officer")
    db.insert_project("p1", "P", {})
    db.insert_batch("b1", "p1", "op")
    db.insert_document("d1", "b1", "p1", validation_status="pending_qc",
                       qc_score=55, page_range="1-2")
    panel = QCReviewPanel(db, None, user)
    qtbot.addWidget(panel)
    panel.refresh()
    assert panel.queue.count() == 1
    assert panel.accept_btn.isEnabled()
    # act on it
    panel.queue.setCurrentRow(0)
    panel._action("accept")
    assert db.get_document("d1")["validation_status"] == "corrected"
    assert panel.queue.count() == 0


def test_qc_buttons_disabled_for_viewer_role(qtbot, db):
    user = login_as(db, "viewer")
    panel = QCReviewPanel(db, None, user)
    qtbot.addWidget(panel)
    assert not panel.accept_btn.isEnabled()


def test_main_window_role_gating(qtbot, db, tmp_path):
    pm = ProfileManager(tmp_path / "projects", db)
    pm.create("Default Project")
    bus = EventBus()

    login_as(db, "operator")
    win = MainWindow(db, pm, bus, staging_dir=tmp_path / "staging")
    qtbot.addWidget(win)
    tab_names = [win.tabs.tabText(i) for i in range(win.tabs.count())]
    assert "Projects" not in tab_names      # operator cannot edit projects
    assert win.new_batch_button.isEnabled()  # but can scan
    Session.logout()

    admin = User("a1", "root", "admin")
    Session._current = admin
    win2 = MainWindow(db, pm, bus, staging_dir=tmp_path / "staging")
    qtbot.addWidget(win2)
    tab_names = [win2.tabs.tabText(i) for i in range(win2.tabs.count())]
    assert "Projects" in tab_names
