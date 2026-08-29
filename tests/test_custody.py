"""Tests for the chain-of-custody module (DOK_Scanner_Enhancement_Plan §3.2)."""
import pytest

from data.db import Database


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
