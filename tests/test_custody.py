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


def test_record_checkpoint_from_barcode_matches_known_bundle(db):
    from custody.bundle_manager import (create_bundle,
                                        record_checkpoint_from_barcode)
    bundle = create_bundle(db, "proj-1", "Box 42")
    matched = record_checkpoint_from_barcode(
        db, bundle["qr_value"], "scanning", "system", batch_id="batch-1")
    assert matched is True
    events = db.list_custody_events(bundle["bundle_id"])
    assert events[-1]["stage"] == "scanning"
    assert events[-1]["batch_id"] == "batch-1"


def test_record_checkpoint_from_barcode_ignores_unrelated_barcode(db):
    from custody.bundle_manager import record_checkpoint_from_barcode
    matched = record_checkpoint_from_barcode(
        db, "SOME-OTHER-DOCUMENT-SEPARATOR", "scanning", "system")
    assert matched is False


def test_record_checkpoint_from_barcode_forwards_audit(db, tmp_path):
    """Global Constraint: every custody action produces an audit trail
    entry - including the automatic pipeline-triggered checkpoint, not
    just the manual UI ones (Task 2's test_record_checkpoint_writes_audit_entry
    already covers those). create_bundle's own "receipt" checkpoint also
    logs via audit when given one, so two rows are expected here, not one
    - this test's own audit instance starts fresh in tmp_path so there's
    no cross-test contamination to account for."""
    from data.audit import AuditLogger
    from custody.bundle_manager import create_bundle, record_checkpoint_from_barcode
    audit = AuditLogger(db, tmp_path / "logs")
    bundle = create_bundle(db, "proj-1", "Box 42", audit=audit)
    record_checkpoint_from_barcode(db, bundle["qr_value"], "scanning",
                                   "system", batch_id="batch-1", audit=audit)
    rows = db.conn.execute(
        "SELECT * FROM audit_log WHERE action='custody_checkpoint'").fetchall()
    assert len(rows) == 2  # receipt (from create_bundle) + scanning
    audit.close()


def test_generate_label_sheet_creates_pdf_with_qr_and_text(tmp_path):
    from custody.bundle_manager import generate_label_sheet
    bundle = {"bundle_id": "b1", "label": "Box 42",
              "qr_value": "DOK-BUNDLE-ABC123456789"}
    out = generate_label_sheet(bundle, tmp_path / "label.pdf")
    assert out.exists()
    assert out.stat().st_size > 0
    # a real PDF starts with the %PDF- magic bytes
    assert out.read_bytes()[:5] == b"%PDF-"


def test_reconciliation_summary_counts_stages_per_bundle(db):
    from custody.bundle_manager import reconciliation_summary, record_checkpoint
    bundle = create_bundle(db, "proj-1", "Box 42")
    record_checkpoint(db, bundle["bundle_id"], "scanning", "system")
    record_checkpoint(db, bundle["bundle_id"], "qc", "alice")
    rows = reconciliation_summary(db, "proj-1")
    assert len(rows) == 1
    row = rows[0]
    assert row["bundle_id"] == bundle["bundle_id"]
    assert row["received"] == 1
    assert row["scanned"] == 1
    assert row["qc"] == 1
    assert row["returned"] == 0


def test_reconciliation_flags_mismatch_when_not_scanned(db):
    from custody.bundle_manager import reconciliation_summary
    create_bundle(db, "proj-1", "Box 42")  # receipt only, never scanned
    rows = reconciliation_summary(db, "proj-1")
    assert rows[0]["mismatch"] is True


def test_reconciliation_flags_mismatch_for_damaged_or_missing_status(db):
    from custody.bundle_manager import (reconciliation_summary,
                                        record_checkpoint)
    bundle = create_bundle(db, "proj-1", "Box 42")
    record_checkpoint(db, bundle["bundle_id"], "scanning", "system")
    record_checkpoint(db, bundle["bundle_id"], "missing", "alice")
    rows = reconciliation_summary(db, "proj-1")
    assert rows[0]["mismatch"] is True


def test_reconciliation_no_mismatch_when_fully_returned(db):
    from custody.bundle_manager import (reconciliation_summary,
                                        record_checkpoint)
    bundle = create_bundle(db, "proj-1", "Box 42")
    for stage in ("scanning", "qc", "storage", "return"):
        record_checkpoint(db, bundle["bundle_id"], stage, "alice")
    rows = reconciliation_summary(db, "proj-1")
    assert rows[0]["mismatch"] is False
