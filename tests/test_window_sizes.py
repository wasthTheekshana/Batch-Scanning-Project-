"""Windows and dialogs must fit a small laptop (1366x768 at 125% Windows
scaling is about 1093x614 usable pixels). A window wider than the screen puts
its right-hand buttons out of reach."""
import pytest
from PyQt6.QtWidgets import QWidget

from ui.event_bus import EventBus
from projects.profile_manager import ProfileManager
from security.rbac import Session
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
    assert admin_window.minimumSizeHint().width() <= SMALL_W, admin_window.minimumSizeHint()


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


def test_the_window_fits_a_small_laptop_in_height_too(admin_window):
    """The Custody tab once needed 582 px by itself, so the whole window was taller than
    a 1366x768 screen at 125% scaling and its bottom buttons were out of reach."""
    assert admin_window.minimumSizeHint().height() <= 614


def test_a_filled_form_keeps_readable_fields_and_the_tables_keep_their_rows(admin_window):
    """With many index fields the form once squashed every box to a sliver, and the
    three custody tables to a couple of rows each."""
    admin_window.resize(1093, 614)
    admin_window.show()
    editor = admin_window.index_editor
    editor.set_zones([{"name": f"field_{i}"} for i in range(18)])
    admin_window.tabs.setCurrentWidget(editor)
    assert all(e.height() >= 22 for e in editor._inputs.values())
    panel = admin_window.custody_panel
    admin_window.tabs.setCurrentWidget(panel)
    panel.views.setCurrentIndex(0)
    assert panel.recon_table.height() >= 140


def test_no_page_however_wide_can_push_the_window_past_the_screen(admin_window):
    """GitHub's Windows machine has wider fonts than the development PC: its pages needed up to 1012 px
    and the window 1132. Whatever a page needs, it scrolls inside the window; the window itself stays small."""
    from PyQt6.QtWidgets import QScrollArea
    tabs = admin_window.tabs
    assert all(isinstance(tabs.stack.widget(i), QScrollArea) for i in range(tabs.count()))
    for i in range(tabs.count()):                         # make every page absurdly wide
        tabs.widget(i).setMinimumWidth(3000)
    admin_window.updateGeometry()
    assert admin_window.minimumSizeHint().width() <= SMALL_W


def test_the_tab_api_still_returns_the_pages_not_their_scroll_areas(admin_window):
    tabs = admin_window.tabs
    assert tabs.widget(tabs.indexOf(admin_window.index_editor)) is admin_window.index_editor
    tabs.setCurrentWidget(admin_window.index_editor)
    assert tabs.currentWidget() is admin_window.index_editor
    assert tabs.indexOf(QWidget()) == -1                 # not one of the pages
