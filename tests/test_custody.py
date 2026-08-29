"""Tests for the chain-of-custody module (DOK_Scanner_Enhancement_Plan §3.2)."""
import pytest

from data.db import Database
from custody.bundle_manager import create_bundle, record_checkpoint, VALID_STAGES


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "custody.db")
    d.insert_project("proj-1", "Test Project", {})
    yield d
    d.close()


def test_insert_and_get_bundle(db):
    db.insert_bundle("b1", "proj-1", "Box 42", "QR-BOX-0042")
    row = db.get_bundle("b1")
    assert row["label"] == "Box 42"
    assert row["qr_value"] == "QR-BOX-0042"
    assert row["status"] == "received"


def test_get_bundle_by_qr(db):
    db.insert_bundle("b1", "proj-1", "Box 42", "QR-BOX-0042")
    row = db.get_bundle_by_qr("QR-BOX-0042")
    assert row["bundle_id"] == "b1"
    assert db.get_bundle_by_qr("NO-SUCH-QR") is None


def test_list_bundles_filters_by_project(db):
    db.insert_project("proj-2", "Other Project", {})
    db.insert_bundle("b1", "proj-1", "Box A", "QR-A")
    db.insert_bundle("b2", "proj-2", "Box B", "QR-B")
    assert [r["bundle_id"] for r in db.list_bundles("proj-1")] == ["b1"]
    assert len(db.list_bundles()) == 2


def test_update_bundle_status(db):
    db.insert_bundle("b1", "proj-1", "Box 42", "QR-BOX-0042")
    db.update_bundle_status("b1", "damaged")
    assert db.get_bundle("b1")["status"] == "damaged"


def test_insert_and_list_custody_events(db):
    db.insert_bundle("b1", "proj-1", "Box 42", "QR-BOX-0042")
    db.insert_custody_event("e1", "b1", "receipt", "alice")
    db.insert_custody_event("e2", "b1", "scanning", "system",
                            batch_id="batch-1", detail={"pages": 12})
    events = db.list_custody_events("b1")
    assert [e["stage"] for e in events] == ["receipt", "scanning"]
    assert events[1]["batch_id"] == "batch-1"


def test_create_bundle_assigns_qr_value_and_logs_receipt(db):
    bundle = create_bundle(db, "proj-1", "Box 42")
    assert bundle["label"] == "Box 42"
    assert bundle["qr_value"]  # non-empty, unique per bundle
    events = db.list_custody_events(bundle["bundle_id"])
    assert [e["stage"] for e in events] == ["receipt"]


def test_create_bundle_qr_values_are_unique(db):
    b1 = create_bundle(db, "proj-1", "Box A")
    b2 = create_bundle(db, "proj-1", "Box B")
    assert b1["qr_value"] != b2["qr_value"]


def test_record_checkpoint_updates_status_and_logs_event(db):
    bundle = create_bundle(db, "proj-1", "Box 42")
    record_checkpoint(db, bundle["bundle_id"], "prep", "alice")
    assert db.get_bundle(bundle["bundle_id"])["status"] == "prep"
    events = db.list_custody_events(bundle["bundle_id"])
    assert [e["stage"] for e in events] == ["receipt", "prep"]


def test_record_checkpoint_rejects_unknown_stage(db):
    bundle = create_bundle(db, "proj-1", "Box 42")
    with pytest.raises(ValueError, match="unknown stage"):
        record_checkpoint(db, bundle["bundle_id"], "not_a_real_stage", "alice")


def test_record_checkpoint_writes_audit_entry(db, tmp_path):
    from data.audit import AuditLogger
    audit = AuditLogger(db, tmp_path / "logs")
    bundle = create_bundle(db, "proj-1", "Box 42", audit=audit)
    record_checkpoint(db, bundle["bundle_id"], "storage", "bob", audit=audit)
    rows = db.conn.execute(
        "SELECT * FROM audit_log WHERE action='custody_checkpoint'").fetchall()
    assert len(rows) == 2
    audit.close()
