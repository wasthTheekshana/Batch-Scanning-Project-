"""Windows and dialogs must fit a small laptop (1366x768 at 125% Windows
scaling is about 1093x614 usable pixels). A window wider than the screen puts
its right-hand buttons out of reach."""
import pytest
from PyQt6.QtWidgets import QWidget

from core.event_bus import EventBus
from core.profile_manager import ProfileManager
from core.rbac import Session
from data.db import Database
from ui.cover_sheet_dialog import CoverSheetDialog
from ui.document_type_dialog import DocumentTypeDialog
from ui.main_window import MainWindow
from ui.theme import fit_to_screen
from ui.zone_dialog import ZoneDialog

SMALL_W, SMALL_H = 1093, 560


@pytest.fixture()
def admin_window(qtbot, tmp_path):
    db = Database(tmp_path / "sizes.db")
    Session.login("admin", "admin", db)
    win = MainWindow(db, ProfileManager(tmp_path / "projects"), EventBus(),
                     staging_dir=tmp_path / "staging")
    qtbot.addWidget(win)
    yield win
    Session.logout()
    db.close()


def test_every_tab_fits_a_small_laptop(admin_window):
    tabs = admin_window.tabs
    too_wide = {tabs.tabText(i): tabs.widget(i).minimumSizeHint().width()
                for i in range(tabs.count())
                if tabs.widget(i).minimumSizeHint().width() > SMALL_W}
    assert not too_wide
    assert admin_window.minimumSizeHint().width() <= SMALL_W


@pytest.mark.parametrize("make", [ZoneDialog, CoverSheetDialog,
                                  DocumentTypeDialog])
def test_dialogs_fit_a_small_laptop(qtbot, make):
    dlg = make()
    qtbot.addWidget(dlg)
    hint = dlg.minimumSizeHint()
    assert hint.width() <= SMALL_W and hint.height() <= SMALL_H


def test_fit_to_screen_never_exceeds_the_screen(qtbot):
    w = QWidget()
    qtbot.addWidget(w)
    fit_to_screen(w, 5000, 5000)
    area = w.screen().availableGeometry()
    assert w.width() <= area.width() and w.height() <= area.height()
    fit_to_screen(w, 300, 200)             # small requests are left alone
    assert (w.width(), w.height()) == (300, 200)


def test_workflow_designer_form_does_not_force_horizontal_scrolling(admin_window):
    """A help label that could not wrap once made the form 3243 px wide, so its
    fields and buttons ran off the right edge of the window."""
    tabs = admin_window.tabs
    designer = next(tabs.widget(i) for i in range(tabs.count())
                    if tabs.tabText(i) == "Projects")
    assert designer.form_body.minimumSizeHint().width() <= SMALL_W - 60
