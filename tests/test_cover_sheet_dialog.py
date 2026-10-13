import fitz
import pytest

from core.event_bus import EventBus
from core.profile_manager import ProfileManager
from core.rbac import Session, User
from data.audit import AuditLogger
from data.db import Database
from ui.cover_sheet_dialog import CoverSheetDialog
from ui.main_window import MainWindow


@pytest.fixture()
def dlg(qtbot):
    d = CoverSheetDialog()
    qtbot.addWidget(d)
    return d


def pick(monkeypatch, path):
    monkeypatch.setattr("ui.cover_sheet_dialog.QFileDialog.getSaveFileName",
                        staticmethod(lambda *a, **k: (str(path), "")))


def test_create_is_disabled_until_there_is_an_id(dlg):
    assert not dlg.create_btn.isEnabled()
    dlg.ids.setPlainText("CIF-1")
    assert dlg.create_btn.isEnabled()
    assert dlg.summary.text() == "1 unique ID(s) to print."


def test_summary_counts_unique_ids_and_reports_skipped_entries(dlg):
    dlg.ids.setPlainText("A-1\nA-1\nB-2\nname with é")
    assert dlg.summary.text().startswith("2 unique ID(s) to print.")
    assert "1 entry skipped" in dlg.summary.text()


def test_a_number_series_is_appended_to_the_list(dlg):
    dlg.ids.setPlainText("CIF-1")
    dlg.prefix.setText("CIF-")
    dlg.start.setValue(100234)
    dlg.count.setValue(3)
    dlg.add_series_btn.click()
    assert dlg.ids.toPlainText().split() == [
        "CIF-1", "CIF-100234", "CIF-100235", "CIF-100236"]


def test_creating_writes_a_pdf_with_one_page_per_id(dlg, tmp_path, monkeypatch):
    out = tmp_path / "sheets.pdf"
    pick(monkeypatch, out)
    dlg.ids.setPlainText("CIF-1\nCIF-2\nCIF-3")
    dlg.create_btn.click()
    assert dlg.last_path == out
    with fitz.open(str(out)) as pdf:
        assert pdf.page_count == 3
    assert dlg.summary.text() == f"Saved {out}"


def test_labels_layout_enables_copies(dlg, tmp_path, monkeypatch):
    assert not dlg.copies.isEnabled()
    dlg.layout_combo.setCurrentIndex(1)
    assert dlg.copies.isEnabled()
    out = tmp_path / "labels.pdf"
    pick(monkeypatch, out)
    dlg.ids.setPlainText("A-1")
    dlg.copies.setValue(4)
    dlg.create_btn.click()
    with fitz.open(str(out)) as pdf:
        assert pdf[0].get_text().split() == ["A-1"] * 4


def test_cancelling_the_file_dialog_writes_nothing(dlg, tmp_path, monkeypatch):
    pick(monkeypatch, "")
    dlg.ids.setPlainText("A-1")
    dlg.create_btn.click()
    assert dlg.last_path is None


def test_an_unprintable_id_is_reported_in_a_message_box(dlg, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr("ui.cover_sheet_dialog.QMessageBox.warning",
                        staticmethod(lambda *a, **k: shown.append(a[2])))
    pick(monkeypatch, tmp_path / "x.pdf")
    dlg.layout_combo.setCurrentIndex(1)
    dlg.ids.setPlainText("X" * 40)
    dlg.create_btn.click()
    assert dlg.last_path is None
    assert len(shown) == 1 and "too long" in shown[0]


def test_the_scan_tab_button_opens_the_dialog(qtbot, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr("ui.cover_sheet_dialog.CoverSheetDialog.exec",
                        lambda self: opened.append(self))
    db = Database(tmp_path / "w.db")
    audit = AuditLogger(db, tmp_path / "logs")
    try:
        ProfileManager(tmp_path / "projects", db).create("P")
        Session._current = User("a1", "root", "admin")
        win = MainWindow(db, ProfileManager(tmp_path / "projects", db),
                         EventBus(), pipeline=None, audit=audit,
                         staging_dir=tmp_path / "staging")
        qtbot.addWidget(win)
        assert win.cover_sheets_button.isEnabled()
        win.cover_sheets_button.click()
        assert len(opened) == 1
    finally:
        Session.logout()
        audit.close()
        db.close()
