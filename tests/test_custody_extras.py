"""Custody extras: an expected document count per bundle, and linking documents
that were scanned before bundles tracked their documents."""

import cv2
import numpy as np
import pytest

from core.rbac import Session, hash_password
from custody.backfill import link_earlier_documents
from custody.bundle_manager import (bundle_summary, create_bundle, link_document,
                                    reconciliation_summary, record_checkpoint)
from data.db import Database


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "x.db")
    d.insert_project("proj-1", "Test Project", {})
    d.insert_batch("b1", "proj-1", "admin")
    yield d
    Session.logout()
    d.close()


def doc(db, doc_id, status="auto_approved", pages=None):
    db.insert_document(doc_id, "b1", "proj-1", page_range="1-1", qc_score=90,
                       validation_status=status, extracted_fields={},
                       qc_detail={"pages": pages or {}})


# -- expected count -----------------------------------------------------------------------------
def test_a_bundle_can_carry_an_expected_document_count(db):
    b = create_bundle(db, "proj-1", "Box 1", expected_documents=3)
    assert db.get_bundle(b["bundle_id"])["expected_documents"] == 3
    assert create_bundle(db, "proj-1", "Box 2")["expected_documents"] is None
    assert create_bundle(db, "proj-1", "Box 3", expected_documents=0)["expected_documents"] is None
    db.set_bundle_expected(b["bundle_id"], 5)
    assert db.get_bundle(b["bundle_id"])["expected_documents"] == 5


def test_the_summary_says_how_many_are_missing_or_extra(db):
    b = create_bundle(db, "proj-1", "Box 1", expected_documents=3)
    for d in ("d1", "d2"):
        doc(db, d)
        link_document(db, b["bundle_id"], d, "b1")
    s = bundle_summary(db, b["bundle_id"])
    assert (s["expected"], s["missing"], s["extra"]) == (3, 1, 0)
    for d in ("d3", "d4"):
        doc(db, d)
        link_document(db, b["bundle_id"], d, "b1")
    s = bundle_summary(db, b["bundle_id"])
    assert (s["missing"], s["extra"]) == (0, 1)
    assert "expected" not in bundle_summary(db, create_bundle(db, "proj-1", "Open")["bundle_id"])


def test_a_scanned_bundle_with_the_wrong_count_is_a_reconciliation_mismatch(db):
    b = create_bundle(db, "proj-1", "Box 1", expected_documents=2)
    record_checkpoint(db, b["bundle_id"], "scanning", "admin", batch_id="b1")
    doc(db, "d1")
    link_document(db, b["bundle_id"], "d1", "b1")
    row = next(r for r in reconciliation_summary(db) if r["bundle_id"] == b["bundle_id"])
    assert row["documents"] == 1 and row["expected"] == 2 and row["mismatch"] is True
    doc(db, "d2")
    link_document(db, b["bundle_id"], "d2", "b1")
    for stage in ("qc", "storage", "return"):
        record_checkpoint(db, b["bundle_id"], stage, "admin")
    row = next(r for r in reconciliation_summary(db) if r["bundle_id"] == b["bundle_id"])
    assert row["mismatch"] is False


def test_a_bundle_without_an_expected_count_is_judged_as_before(db):
    b = create_bundle(db, "proj-1", "Box 1")
    record_checkpoint(db, b["bundle_id"], "scanning", "admin")
    for stage in ("qc", "storage", "return"):
        record_checkpoint(db, b["bundle_id"], stage, "admin")
    row = reconciliation_summary(db)[0]
    assert row["mismatch"] is False and row["expected"] is None


# -- linking earlier documents --------------------------------------------------------------------
def page_file(tmp_path, name):
    path = tmp_path / name
    cv2.imwrite(str(path), np.full((20, 20), 255, np.uint8))
    return str(path)


def test_earlier_documents_are_linked_from_their_saved_page_images(db, tmp_path):
    b = create_bundle(db, "proj-1", "Box 1")
    labelled = page_file(tmp_path, "a.png")
    plain = page_file(tmp_path, "b.png")
    doc(db, "d1", pages={"1": {"original_path": labelled}})
    doc(db, "d2", pages={"1": {"original_path": plain}})
    doc(db, "d3", pages={"1": {"original_path": str(tmp_path / "gone.png")}})
    doc(db, "d4", pages={})

    def decode(path):
        return [{"value": b["qr_value"]}] if path == labelled else []

    result = link_earlier_documents(db, "admin", decode=decode)
    assert result == {"documents": 4, "linked": 1, "no_label": 1, "no_images": 2}
    assert [r["document_id"] for r in db.list_bundle_documents(b["bundle_id"])] == ["d1"]


def test_linking_earlier_documents_can_complete_a_bundles_qc_stage(db, tmp_path):
    b = create_bundle(db, "proj-1", "Box 1")
    record_checkpoint(db, b["bundle_id"], "scanning", "admin", batch_id="b1")
    labelled = page_file(tmp_path, "a.png")
    doc(db, "d1", "auto_approved", pages={"1": {"original_path": labelled}})
    link_earlier_documents(db, "admin", decode=lambda p: [{"value": b["qr_value"]}])
    assert db.get_bundle(b["bundle_id"])["status"] == "qc"


def test_linking_twice_changes_nothing_more(db, tmp_path):
    b = create_bundle(db, "proj-1", "Box 1")
    labelled = page_file(tmp_path, "a.png")
    doc(db, "d1", pages={"1": {"original_path": labelled}})
    decode = lambda p: [{"value": b["qr_value"]}]
    assert link_earlier_documents(db, "admin", decode=decode)["linked"] == 1
    assert link_earlier_documents(db, "admin", decode=decode)["documents"] == 0


def test_an_unreadable_page_does_not_stop_the_rest(db, tmp_path):
    b = create_bundle(db, "proj-1", "Box 1")
    good, bad = page_file(tmp_path, "g.png"), page_file(tmp_path, "x.png")
    doc(db, "d1", pages={"1": {"original_path": bad}})
    doc(db, "d2", pages={"1": {"original_path": good}})

    def decode(path):
        if path == bad:
            raise RuntimeError("corrupt")
        return [{"value": b["qr_value"]}]

    result = link_earlier_documents(db, "admin", decode=decode)
    assert result["linked"] == 1 and result["no_label"] == 1


# -- the Custody tab ----------------------------------------------------------------------------------------
def admin(db):
    db.insert_user("u-a", "adm", hash_password("pw"), "admin")
    return Session.login("adm", "pw", db)


def test_the_tab_creates_a_bundle_with_an_expected_count_and_shows_the_gap(qtbot, db):
    from ui.custody_panel import CustodyPanel
    panel = CustodyPanel(db, None, admin(db))
    qtbot.addWidget(panel)
    panel.new_bundle_label.setText("Box 9")
    panel.new_expected.setValue(4)
    panel.create_bundle()
    assert panel.new_expected.value() == 0
    panel.bundle_list.setCurrentRow(0)
    assert panel.expected_spin.value() == 4
    assert "4 expected" in panel.summary_label.text()
    b = db.list_bundles()[0]
    doc(db, "d1")
    link_document(db, b["bundle_id"], "d1", "b1")
    panel.refresh()
    assert "EXPECTED 4: 3 not scanned yet" in panel.summary_label.text()
    assert panel.recon_table.item(0, 7).text() == "1" and panel.recon_table.item(0, 8).text() == "4"


def test_the_expected_count_can_be_changed_later(qtbot, db):
    from ui.custody_panel import CustodyPanel
    b = create_bundle(db, "proj-1", "Box 1")
    panel = CustodyPanel(db, None, admin(db))
    qtbot.addWidget(panel)
    panel.bundle_list.setCurrentRow(0)
    panel.expected_spin.setValue(7)
    panel.set_expected()
    assert db.get_bundle(b["bundle_id"])["expected_documents"] == 7
    assert panel._selected_bundle_id() == b["bundle_id"]


def test_the_tab_links_earlier_documents(qtbot, db, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    import custody.backfill as backfill
    from ui.custody_panel import CustodyPanel
    b = create_bundle(db, "proj-1", "Box 1")
    labelled = page_file(tmp_path, "a.png")
    doc(db, "d1", pages={"1": {"original_path": labelled}})
    monkeypatch.setattr(backfill, "link_earlier_documents",
                        lambda db_, user, audit=None: link_earlier_documents(
                            db_, user, audit=audit, decode=lambda p: [{"value": b["qr_value"]}]))
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    panel = CustodyPanel(db, None, admin(db))
    qtbot.addWidget(panel)
    assert panel.link_earlier()["linked"] == 1
    assert "1 linked" in shown[0]
