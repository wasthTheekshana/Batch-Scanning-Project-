"""The Search tab: finding documents, who may see what, the result actions."""
import pytest

from core.rbac import Session, hash_password
from data.db import Database
from ui.search_panel import SearchPanel


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "sp.db")
    d.insert_project("p1", "Bank - Customer file", {})
    d.insert_batch("b1", "p1", "alice")
    d.insert_batch("b2", "p1", "bob")
    pdf = tmp_path / "d1.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    for doc_id, batch, fields, path in (
            ("d1", "b1", {"barcode": "CIF-1", "nic_number": "199012345678"}, str(pdf)),
            ("d2", "b2", {"barcode": "CIF-2", "nic_number": "856789012V"}, str(tmp_path / "gone.pdf"))):
        d.insert_document(doc_id, batch, "p1", page_range="1-2", qc_score=90,
                          validation_status="auto_approved", extracted_fields=fields)
        d.update_document(doc_id, delivery_status="delivered_local", storage_path=path)
    yield d
    Session.logout()
    d.close()


def login_as(db, role):
    db.insert_user(f"u-{role}", f"{role}1", hash_password("pw"), role)
    return Session.login(f"{role}1", "pw", db)


def make(qtbot, db, role="admin"):
    panel = SearchPanel(db, login_as(db, role))
    qtbot.addWidget(panel)
    return panel


def barcodes(panel):
    return [r["fields"]["barcode"] for r in panel._rows]


def test_every_role_may_search(db):
    for role in ("admin", "operator", "qc_officer", "viewer"):
        assert login_as(db, role).can("document.search")
        Session.logout()


def test_searching_by_a_value_lists_the_document(qtbot, db):
    panel = make(qtbot, db)
    panel.text.setText("856789")
    panel.search()
    assert barcodes(panel) == ["CIF-2"] and panel.table.rowCount() == 1
    assert "1 document" in panel.summary.text()


def test_no_match_says_so(qtbot, db):
    panel = make(qtbot, db)
    panel.text.setText("zzzz")
    panel.search()
    assert panel.table.rowCount() == 0 and "No documents" in panel.summary.text()


def test_clear_resets_the_filters_and_lists_everything(qtbot, db):
    panel = make(qtbot, db)
    panel.text.setText("zzzz")
    panel.search()
    panel.clear()
    assert panel.text.text() == "" and panel.table.rowCount() == 2


def test_the_field_filter_uses_the_known_field_names(qtbot, db):
    panel = make(qtbot, db)
    assert panel.field.findText("nic_number") >= 0
    panel.field.setCurrentIndex(panel.field.findText("nic_number"))
    panel.field_value.setText("1990")
    panel.search()
    assert barcodes(panel) == ["CIF-1"]


def test_an_operator_only_finds_their_own_batches(qtbot, db):
    db.insert_user("u-alice", "alice", hash_password("pw"), "operator")
    panel = SearchPanel(db, Session.login("alice", "pw", db))
    qtbot.addWidget(panel)
    panel.search()
    assert barcodes(panel) == ["CIF-1"]


def test_load_more_adds_the_next_page(qtbot, db, monkeypatch):
    import ui.search_panel as sp
    monkeypatch.setattr(sp, "PAGE", 1)
    panel = make(qtbot, db)
    panel.search()
    assert panel.table.rowCount() == 1 and not panel.more_btn.isHidden()
    panel.load_more()
    assert panel.table.rowCount() == 2 and panel.more_btn.isHidden()


def test_actions_follow_the_selection(qtbot, db):
    panel = make(qtbot, db, "admin")
    panel.search()
    assert not panel.open_btn.isEnabled() and not panel.edit_btn.isEnabled()
    panel.table.selectRow(0)
    assert panel.open_btn.isEnabled() and panel.timeline_btn.isEnabled()
    assert panel.edit_btn.isEnabled()


def test_a_viewer_cannot_edit_index_fields(qtbot, db):
    panel = make(qtbot, db, "viewer")
    panel.search()
    panel.table.selectRow(0)
    assert panel.timeline_btn.isEnabled() and not panel.edit_btn.isEnabled()


def test_edit_asks_the_main_window_to_open_the_document(qtbot, db):
    panel = make(qtbot, db)
    panel.search()
    panel.table.selectRow(0)
    with qtbot.waitSignal(panel.edit_requested) as sig:
        panel.edit_btn.click()
    assert sig.args == [panel.selected()["document_id"]]


def test_opening_a_missing_pdf_warns_instead_of_failing(qtbot, db, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a))
    opened = []
    monkeypatch.setattr("ui.search_panel.QDesktopServices.openUrl",
                        lambda url: opened.append(url))
    panel = make(qtbot, db)
    panel.text.setText("CIF-2")                 # its PDF is gone
    panel.search()
    panel.table.selectRow(0)
    panel.open_pdf()
    assert warned and not opened
    panel.text.setText("CIF-1")                 # its PDF exists
    panel.search()
    panel.table.selectRow(0)
    panel.open_pdf()
    assert len(opened) == 1


def test_history_dialog_lists_the_document_trail(qtbot, db, monkeypatch):
    from ui import search_panel
    shown = []
    monkeypatch.setattr(search_panel.TimelineDialog, "exec",
                        lambda self: shown.append(self.table.rowCount()))
    panel = make(qtbot, db)
    panel.search()
    panel.table.selectRow(0)
    panel.show_history()
    assert len(shown) == 1


def test_main_window_has_a_search_tab_that_opens_the_index(qtbot, db, tmp_path):
    from core.event_bus import EventBus
    from core.profile_manager import ProfileManager
    from ui.main_window import MainWindow
    user = login_as(db, "admin")
    win = MainWindow(db, ProfileManager(tmp_path / "profiles"), EventBus(), staging_dir=tmp_path / "staging")
    qtbot.addWidget(win)
    names = [win.tabs.tabText(i) for i in range(win.tabs.count())]
    assert "Search" in names
    win.search_panel.edit_requested.emit("d1")
    assert win.tabs.currentWidget() is win.index_editor
