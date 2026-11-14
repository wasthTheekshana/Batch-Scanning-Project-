"""A custody label QR that no bundle in this database owns."""
import json

from tests.test_barcode_roles import _page_counts, _run, db  # noqa: F401


def test_an_unknown_custody_label_is_left_out_and_never_becomes_a_document_barcode(
        tmp_path, db, monkeypatch):
    docs = _run(tmp_path, db, {"strategy": ["barcode"]},
                {1: ["CIF-1"], 3: ["DOK-BUNDLE-FFFFFFFFFFFF"]},
                ["text", "text", "text", "text"], monkeypatch)
    assert _page_counts(docs) == [1, 2]      # pages 1-2 and 4; the label sheet is out
    barcodes = sorted(json.loads(d["extracted_fields"]).get("barcode", "") for d in docs)
    assert barcodes == ["", "CIF-1"]         # the label never labels page 4


def test_a_known_custody_label_still_logs_its_checkpoint(tmp_path, db, monkeypatch):
    from custody.bundle_manager import create_bundle
    bundle = create_bundle(db, "proj-1", "Box 1")
    docs = _run(tmp_path, db, {"strategy": ["barcode"]},
                {1: ["CIF-1"], 3: [bundle["qr_value"]]},
                ["text", "text", "text", "text"], monkeypatch)
    assert _page_counts(docs) == [1, 2]
    events = [r["stage"] for r in db.conn.execute(
        "select stage from custody_events where bundle_id=?", (bundle["bundle_id"],))]
    assert "scanning" in events


def test_the_operator_is_warned_once_per_batch(qtbot, tmp_path, monkeypatch):
    from ui.event_bus import EventBus
    from projects.profile_manager import ProfileManager
    from security.rbac import Session
    from data.db import Database
    from ui.main_window import MainWindow
    d = Database(tmp_path / "w.db")
    Session.login("admin", "admin", d)
    bus = EventBus()
    win = MainWindow(d, ProfileManager(tmp_path / "profiles"), bus,
                     staging_dir=tmp_path / "staging")
    qtbot.addWidget(win)
    shown = []
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "show", lambda self: shown.append(self.text()))
    bus.publish_pipeline_event({"stage": "custody", "status": "unknown_label",
                                "batch_id": "b1", "page_no": 3})
    bus.publish_pipeline_event({"stage": "custody", "status": "unknown_label",
                                "batch_id": "b1", "page_no": 5})
    bus.publish_pipeline_event({"stage": "custody", "status": "unknown_label",
                                "batch_id": "b2", "page_no": 1})
    assert len(shown) == 2 and "Page 3" in shown[0]
    Session.logout()
    d.close()


def test_unreadable_projects_are_reported_at_startup(qtbot, tmp_path, monkeypatch):
    from ui.event_bus import EventBus
    from projects.profile_manager import ProfileManager
    from security.rbac import Session
    from data.db import Database
    from ui.main_window import MainWindow
    d = Database(tmp_path / "u.db")
    Session.login("admin", "admin", d)
    win = MainWindow(d, ProfileManager(tmp_path / "profiles"), EventBus(),
                     staging_dir=tmp_path / "staging")
    qtbot.addWidget(win)
    shown = []
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.text()))
    assert win.report_unreadable_projects() == 0 and not shown      # nothing wrong: silent
    d.insert_project("bad", "Broken project", {})
    d.conn.execute("update projects set config_json='{broken' where project_id='bad'")
    d.conn.commit()
    assert win.report_unreadable_projects() == 1
    assert "Broken project" in shown[0]
    Session.logout()
    d.close()
