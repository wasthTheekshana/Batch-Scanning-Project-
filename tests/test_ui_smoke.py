"""UI smoke tests — offscreen Qt via pytest-qt."""
import cv2
import numpy as np
import pytest

from core.event_bus import EventBus
from core.profile_manager import ProfileManager
from core.rbac import Session, User, hash_password
from data.db import Database
from tests.conftest import make_text_page
from ui.image_viewer import ImageViewer
from ui.job_monitor import JobMonitor
from ui.login_dialog import ChangePasswordDialog, LoginDialog, require_password_change
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


def test_np_to_pixmap_accepts_noncontiguous_arrays(qtbot):
    # auto_crop/crop_rect return numpy slices whose buffers are strided;
    # np_to_pixmap must render them regardless of contiguity
    from ui.page_editor import np_to_pixmap

    gray = np.full((100, 80), 200, np.uint8)[10:90, 10:70]
    assert not gray.flags["C_CONTIGUOUS"]
    pm = np_to_pixmap(gray)
    assert (pm.width(), pm.height()) == (60, 80)

    color = np.full((100, 80, 3), 120, np.uint8)[5:95, 5:75]
    assert not color.flags["C_CONTIGUOUS"]
    pm = np_to_pixmap(color)
    assert (pm.width(), pm.height()) == (70, 90)


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
    panel.filter_combo.setCurrentIndex(panel.filter_combo.findData("pending"))   # the review queue
    qtbot.addWidget(panel)
    panel.refresh()
    assert panel.queue.count() == 1
    assert panel.accept_btn.isEnabled()
    # act on it
    panel.queue.setCurrentRow(0)
    panel._action("accept")
    assert db.get_document("d1")["validation_status"] == "corrected"
    assert panel.queue.count() == 0


def test_qc_detail_does_not_inflate_minimum_width(qtbot, db):
    """Regression: long qc_detail text forced a 2253px minimum window
    width (wider than a 1920px screen)."""
    user = login_as(db, "qc_officer")
    db.insert_project("p1", "P", {})
    db.insert_batch("b1", "p1", "op")
    long_detail = {"pages": {"1": {
        "advanced": {"deduction": 20, "laplacian_variance": 12.3,
                     "background_std": 4.2, "extra": "x" * 300},
        "color": {"deduction": 10, "michelson_contrast": 0.21},
        "flags": ["blur_warn", "low_contrast"]}}}
    db.insert_document("d1", "b1", "p1", validation_status="pending_qc",
                       qc_score=70, page_range="1-1", qc_detail=long_detail)
    panel = QCReviewPanel(db, None, user)
    panel.filter_combo.setCurrentIndex(panel.filter_combo.findData("pending"))   # the review queue
    qtbot.addWidget(panel)
    panel.refresh()
    panel.queue.setCurrentRow(0)
    assert "QC score: 70" in panel.score_label.text()
    assert "advanced −20" in panel.score_label.text()
    assert panel.score_label.wordWrap()
    assert panel.minimumSizeHint().width() < 1200


def test_qc_buttons_disabled_for_viewer_role(qtbot, db):
    user = login_as(db, "viewer")
    panel = QCReviewPanel(db, None, user)
    panel.filter_combo.setCurrentIndex(panel.filter_combo.findData("pending"))   # the review queue
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


def test_custody_tab_gated_by_role(qtbot, db, tmp_path):
    pm = ProfileManager(tmp_path / "projects", db)
    pm.create("Default Project")
    bus = EventBus()

    # operator has custody.manage -> Custody tab present
    login_as(db, "operator")
    win = MainWindow(db, pm, bus, staging_dir=tmp_path / "staging")
    qtbot.addWidget(win)
    tab_names = [win.tabs.tabText(i) for i in range(win.tabs.count())]
    assert "Custody" in tab_names
    assert hasattr(win, "custody_panel")
    Session.logout()

    # viewer lacks custody.manage -> no Custody tab
    login_as(db, "viewer")
    win2 = MainWindow(db, pm, bus, staging_dir=tmp_path / "staging")
    qtbot.addWidget(win2)
    tab_names2 = [win2.tabs.tabText(i) for i in range(win2.tabs.count())]
    assert "Custody" not in tab_names2
    assert not hasattr(win2, "custody_panel")
    Session.logout()


def test_default_password_forces_a_change(qtbot, db, monkeypatch):
    user = Session.login("admin", "admin", db)
    assert user.must_change_password

    def fill_and_accept(self):
        self.new_password.setText("a-better-pass-1")
        self.confirm.setText("a-better-pass-1")
        self.try_change()
        return self.DialogCode.Accepted if self.chosen else self.DialogCode.Rejected
    monkeypatch.setattr(ChangePasswordDialog, "exec", fill_and_accept)
    assert require_password_change(db) is True
    assert Session.current_user().must_change_password is False
    Session.logout()
    assert Session.login("admin", "a-better-pass-1", db)


def test_cancelling_the_forced_change_signs_out(qtbot, db, monkeypatch):
    Session.login("admin", "admin", db)
    monkeypatch.setattr(ChangePasswordDialog, "exec",
                        lambda self: self.DialogCode.Rejected)
    assert require_password_change(db) is False
    assert Session.current_user() is None


def test_change_dialog_refuses_mismatch_and_weak_passwords(qtbot, db):
    user = Session.login("admin", "admin", db)
    dlg = ChangePasswordDialog(db, user)
    qtbot.addWidget(dlg)
    dlg.new_password.setText("a-better-pass-1")
    dlg.confirm.setText("different-pass-1")
    dlg.try_change()
    assert "match" in dlg.error_label.text() and dlg.chosen == ""
    dlg.new_password.setText("short")
    dlg.confirm.setText("short")
    dlg.try_change()
    assert "at least" in dlg.error_label.text() and dlg.chosen == ""
