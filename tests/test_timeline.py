"""Unified audit timeline: merges custody events and processing audit rows."""
import csv
import time

import pytest

from core.models import new_id
from custody.bundle_manager import create_bundle, record_checkpoint
from custody.timeline import (batch_timeline, bundle_timeline,
                              document_timeline, write_timeline_csv,
                              write_timeline_pdf)
from data.audit import AuditLogger
from data.db import Database


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    d.insert_project("proj-1", "Test Project", {})
    yield d
    d.close()


@pytest.fixture()
def audit(db, tmp_path):
    a = AuditLogger(db, tmp_path / "logs")
    yield a
    a.close()


def _document(db, batch_id, doc_id=None):
    doc_id = doc_id or new_id()
    db.insert_document(doc_id, batch_id, "proj-1", page_range="1-1",
                       enhancement_profile="default", extracted_fields={},
                       validation_status="auto_approved", qc_score=90,
                       qc_detail={}, duplicate_of=None)
    return doc_id


@pytest.fixture()
def lifecycle(db, audit):
    """bundle receipt -> scan batch -> doc rule/export/deliver -> storage."""
    tick = lambda: time.sleep(0.003)  # timestamps are ms-resolution
    bundle = create_bundle(db, "proj-1", "Box 42", audit=audit)
    db.insert_batch("batch-1", "proj-1", "op1")
    tick()
    record_checkpoint(db, bundle["bundle_id"], "scanning", "op1",
                      batch_id="batch-1", audit=audit)
    tick()
    audit.log("scan", "op1", batch_id="batch-1", pages=3)
    doc_id = _document(db, "batch-1")
    tick()
    audit.log("rule_engine", "system", document_id=doc_id, status="ok")
    tick()
    audit.log("export", "system", document_id=doc_id)
    tick()
    audit.log("deliver", "system", document_id=doc_id)
    tick()
    record_checkpoint(db, bundle["bundle_id"], "storage", "op2", audit=audit)
    # noise from an unrelated batch / document
    db.insert_batch("batch-other", "proj-1", "op9")
    other = _document(db, "batch-other")
    audit.log("scan", "op9", batch_id="batch-other", pages=1)
    audit.log("export", "system", document_id=other)
    return {"bundle_id": bundle["bundle_id"], "doc_id": doc_id}


def test_bundle_timeline_merges_custody_and_processing(db, lifecycle):
    entries = bundle_timeline(db, lifecycle["bundle_id"])
    actions = [(e["source"], e["action"]) for e in entries]
    assert actions == [
        ("custody", "receipt"), ("custody", "scanning"), ("audit", "scan"),
        ("audit", "rule_engine"), ("audit", "export"), ("audit", "deliver"),
        ("custody", "storage")]


def test_timestamps_are_chronological(db, lifecycle):
    entries = bundle_timeline(db, lifecycle["bundle_id"])
    stamps = [e["timestamp"] for e in entries]
    assert stamps == sorted(stamps)


def test_custody_checkpoint_is_not_duplicated(db, lifecycle):
    entries = bundle_timeline(db, lifecycle["bundle_id"])
    assert not [e for e in entries if e["action"] == "custody_checkpoint"]
    assert len([e for e in entries if e["action"] == "scanning"]) == 1


def test_unrelated_batches_do_not_leak(db, lifecycle):
    entries = bundle_timeline(db, lifecycle["bundle_id"])
    assert all(e["batch_id"] != "batch-other" for e in entries)
    assert len([e for e in entries if e["action"] == "export"]) == 1


def test_entries_carry_ids_for_linking(db, lifecycle):
    entries = bundle_timeline(db, lifecycle["bundle_id"])
    export = next(e for e in entries if e["action"] == "export")
    assert export["document_id"] == lifecycle["doc_id"]
    assert export["batch_id"] == "batch-1"
    assert export["bundle_id"] == lifecycle["bundle_id"]


def test_bundle_without_batches_has_only_custody_events(db, audit):
    bundle = create_bundle(db, "proj-1", "Empty box", audit=audit)
    entries = bundle_timeline(db, bundle["bundle_id"])
    assert [(e["source"], e["action"]) for e in entries] == [
        ("custody", "receipt")]


def test_batch_timeline_includes_linked_custody(db, lifecycle):
    entries = batch_timeline(db, "batch-1")
    sources = {(e["source"], e["action"]) for e in entries}
    assert ("custody", "scanning") in sources
    assert ("audit", "scan") in sources
    assert ("audit", "deliver") in sources
    assert ("custody", "receipt") not in sources  # no batch on that event


def test_document_timeline_is_that_documents_audit_rows(db, lifecycle):
    entries = document_timeline(db, lifecycle["doc_id"])
    assert [e["action"] for e in entries] == [
        "rule_engine", "export", "deliver"]
    assert all(e["batch_id"] == "batch-1" for e in entries)


def test_unknown_ids_give_empty_timelines(db):
    assert bundle_timeline(db, "nope") == []
    assert batch_timeline(db, "nope") == []
    assert document_timeline(db, "nope") == []


def test_write_timeline_csv(db, lifecycle, tmp_path):
    entries = bundle_timeline(db, lifecycle["bundle_id"])
    out = write_timeline_csv(entries, tmp_path / "sub" / "timeline.csv")
    with open(out, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == len(entries)
    assert {"timestamp", "source", "action", "user", "bundle_id",
            "batch_id", "document_id", "detail"} <= set(rows[0])
    assert rows[0]["action"] == "receipt"


def test_write_timeline_pdf(db, lifecycle, tmp_path):
    entries = bundle_timeline(db, lifecycle["bundle_id"])
    out = write_timeline_pdf(entries, tmp_path / "timeline.pdf",
                             title="Bundle Box 42")
    assert out.exists() and out.read_bytes().startswith(b"%PDF")
    # empty input still produces a valid PDF
    empty = write_timeline_pdf([], tmp_path / "empty.pdf", title="Nothing")
    assert empty.read_bytes().startswith(b"%PDF")


# ---- export helper -------------------------------------------------------------

def test_export_timeline_picks_format_by_suffix(db, lifecycle, tmp_path):
    from custody.timeline import export_timeline
    entries = bundle_timeline(db, lifecycle["bundle_id"])
    csv_out = export_timeline(entries, tmp_path / "a.csv", "t")
    pdf_out = export_timeline(entries, tmp_path / "a.pdf", "t")
    assert csv_out.read_text(encoding="utf-8").startswith("timestamp,")
    assert pdf_out.read_bytes().startswith(b"%PDF")
    with pytest.raises(ValueError):
        export_timeline(entries, tmp_path / "a.txt", "t")


# ---- UI --------------------------------------------------------------------------

def _panel_user():
    from core.rbac import User
    return User("u1", "alice", "admin")


def test_custody_panel_shows_bundle_timeline(qtbot, db, audit, lifecycle):
    from ui.custody_panel import CustodyPanel
    panel = CustodyPanel(db, audit, _panel_user())
    qtbot.addWidget(panel)
    assert panel.timeline_table.rowCount() == 0
    panel.bundle_list.setCurrentRow(0)
    assert panel.timeline_table.rowCount() == len(
        bundle_timeline(db, lifecycle["bundle_id"]))
    actions = [panel.timeline_table.item(r, 2).text()
               for r in range(panel.timeline_table.rowCount())]
    assert actions[0] == "receipt" and "deliver" in actions


def test_custody_panel_exports_timeline_and_audits(
        qtbot, db, audit, lifecycle, tmp_path, monkeypatch):
    from ui.custody_panel import CustodyPanel
    out = tmp_path / "bundle.csv"
    monkeypatch.setattr(
        "ui.custody_panel.QFileDialog.getSaveFileName",
        staticmethod(lambda *a, **k: (str(out), "")))
    panel = CustodyPanel(db, audit, _panel_user())
    qtbot.addWidget(panel)
    panel.bundle_list.setCurrentRow(0)
    panel.export_timeline_btn.click()
    assert out.exists()
    row = db.conn.execute(
        "SELECT * FROM audit_log WHERE action='timeline_export'").fetchone()
    assert row is not None and row["user"] == "alice"


def test_reports_panel_exports_batch_timeline(
        qtbot, db, audit, lifecycle, tmp_path, monkeypatch):
    from ui.reports_panel import ReportsPanel
    out = tmp_path / "batch.pdf"
    monkeypatch.setattr(
        "ui.reports_panel.QFileDialog.getSaveFileName",
        staticmethod(lambda *a, **k: (str(out), "")))
    panel = ReportsPanel(db, _panel_user())
    qtbot.addWidget(panel)
    for i in range(panel.batch_list.count()):
        panel.batch_list.setCurrentRow(i)
        if panel.current_batch_id() == "batch-1":
            break
    assert panel.timeline_btn.isEnabled()
    panel.timeline_btn.click()
    assert out.read_bytes().startswith(b"%PDF")
    assert db.conn.execute(
        "SELECT 1 FROM audit_log WHERE action='timeline_export'").fetchone()
