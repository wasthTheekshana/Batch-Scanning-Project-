"""Usability: a button that needs a selection is off until there is one, and a
table heading is never clipped."""
import pytest

from core.rbac import Session, hash_password
from custody.bundle_manager import create_bundle
from data.db import Database


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "u.db")
    d.insert_project("p1", "P", {})
    d.insert_batch("b1", "p1", "admin")
    yield d
    Session.logout()
    d.close()


def admin(db):
    db.insert_user("u-a", "adm", hash_password("pw"), "admin")
    return Session.login("adm", "pw", db)


def test_custody_actions_wait_for_a_selected_bundle(qtbot, db):
    from ui.custody_panel import CustodyPanel
    create_bundle(db, "p1", "Box 1")
    panel = CustodyPanel(db, None, admin(db))
    qtbot.addWidget(panel)
    acting = (panel.print_label_btn, panel.record_checkpoint_btn, panel.flag_incident_btn,
              panel.set_expected_btn, panel.export_timeline_btn)
    assert panel._selected_bundle_id() is None
    assert not any(w.isEnabled() for w in acting)
    panel.bundle_list.setCurrentRow(0)
    assert all(w.isEnabled() for w in acting)
    panel.bundle_list.setCurrentRow(-1)
    assert not any(w.isEnabled() for w in acting)
    assert panel.create_bundle_btn.isEnabled() and panel.link_earlier_btn.isEnabled()   # these need none


def test_an_operator_never_gets_the_timeline_export_even_with_a_bundle_selected(qtbot, db):
    from ui.custody_panel import CustodyPanel
    db.insert_user("u-o", "oper", hash_password("pw"), "operator")   # no report.export right
    create_bundle(db, "p1", "Box 1")
    panel = CustodyPanel(db, None, Session.login("oper", "pw", db))
    qtbot.addWidget(panel)
    panel.bundle_list.setCurrentRow(0)
    assert not panel.export_timeline_btn.isEnabled()


def test_no_custody_table_heading_is_clipped(qtbot, db):
    from ui.custody_panel import CustodyPanel
    panel = CustodyPanel(db, None, admin(db))
    qtbot.addWidget(panel)
    panel.resize(1100, 700)
    panel.show()
    for table in (panel.recon_table, panel.docs_table, panel.timeline_table):
        header = table.horizontalHeader()
        metrics = header.fontMetrics()
        for col in range(table.columnCount()):
            label = table.horizontalHeaderItem(col).text()
            assert header.sectionSize(col) >= metrics.horizontalAdvance(label), (label, header.sectionSize(col))


def index_editor(qtbot, db, zones):
    from ui.index_editor import IndexEditor
    ed = IndexEditor(db, zones, None, admin(db),
                     zones_for_project=lambda pid: (zones, False))
    qtbot.addWidget(ed)
    return ed


def test_index_save_and_extract_wait_for_a_document(qtbot, db):
    db.insert_document("d1", "b1", "p1", page_range="1-1", qc_score=90,
                       validation_status="auto_approved", extracted_fields={})
    ed = index_editor(qtbot, db, [{"name": "nic_number"}])
    assert not ed.save_button.isEnabled() and not ed.extract_btn.isEnabled()
    ed.load_document("d1")
    assert ed.save_button.isEnabled() and ed.extract_btn.isEnabled()


def test_index_buttons_stay_off_when_the_project_has_no_fields(qtbot, db):
    db.insert_document("d1", "b1", "p1", page_range="1-1", qc_score=90,
                       validation_status="auto_approved", extracted_fields={})
    ed = index_editor(qtbot, db, [])
    ed.load_document("d1")
    assert not ed.save_button.isEnabled() and not ed.extract_btn.isEnabled()
    assert "Workflow Designer" in ed.hint.text()
