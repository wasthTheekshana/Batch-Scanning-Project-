"""An operator splits a wrongly joined document or merges a wrongly cut one."""
import json

import pytest
from PyQt6.QtWidgets import QMessageBox

from pipeline.doc_edit import DocumentEditError, merge_documents, split_document, why_not_editable
from security.rbac import Session
from data.db import Database


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    d.insert_project("p1", "P", {})
    d.insert_batch("b1", "p1", "admin")
    d.insert_batch("b2", "p1", "admin")
    yield d
    Session.logout()
    d.close()


class Audit:
    def __init__(self):
        self.events = []

    def log(self, action, user, **detail):
        self.events.append((action, user, detail))


def add(db, doc_id, pages, batch="b1", status="pending_qc", fields=None, delivered=False, types=None):
    detail = {"pages": {str(n): {"working_path": f"/w/{n}.png", "original_path": f"/o/{n}.png",
                                 **({"doc_type": types[i]} if types else {})}
                        for i, n in enumerate(pages)}, "flags": ["blur_warn"]}
    db.insert_document(doc_id, batch, "p1", page_range=f"{pages[0]}-{pages[-1]}", qc_score=88,
                       validation_status=status, extracted_fields=fields or {"barcode": "CIF-1"},
                       qc_detail=detail, storage_path="/out/x.pdf" if delivered else None,
                       delivery_detail={"local": {"success": True}} if delivered else {})


def detail(db, doc_id):
    return json.loads(db.get_document(doc_id)["qc_detail"])


# -- split ------------------------------------------------------------------------------------
def test_a_document_is_split_into_two_that_wait_for_review(db):
    add(db, "d1", [1, 2, 3, 4], fields={"barcode": "CIF-1", "nic_number": "553216789V"},
        types=["Application", "Application", "NIC", "NIC"])
    audit = Audit()
    result = split_document(db, audit, "kamal", "d1", 3)
    first, second = (db.get_document(i) for i in result.new_ids)
    assert (first["page_range"], second["page_range"]) == ("1-2", "3-4")
    assert first["validation_status"] == second["validation_status"] == "pending_qc"
    assert sorted(detail(db, result.new_ids[0])["pages"]) == ["1", "2"]
    assert sorted(detail(db, result.new_ids[1])["pages"]) == ["3", "4"]
    assert json.loads(first["extracted_fields"])["nic_number"] == "553216789V"      # the first keeps its values
    assert "nic_number" not in json.loads(second["extracted_fields"])               # the second is indexed afresh
    assert json.loads(second["extracted_fields"])["doc_types"] == "NIC"
    assert "low_confidence_index" in detail(db, result.new_ids[1])["flags"]          # so the Index tab asks
    original = db.get_document("d1")
    assert original["validation_status"] == "rejected"
    assert detail(db, "d1")["superseded_by"] == result.new_ids and "superseded" in detail(db, "d1")["flags"]
    assert audit.events[0][0] == "document_split" and audit.events[0][1] == "kamal"


def test_a_split_needs_a_page_after_the_first_and_two_pages(db):
    add(db, "d1", [1, 2, 3])
    add(db, "one", [5])
    for page in (1, 9):
        with pytest.raises(DocumentEditError):
            split_document(db, None, "u", "d1", page)
    with pytest.raises(DocumentEditError, match="one-page"):
        split_document(db, None, "u", "one", 5)
    assert db.get_document("d1")["validation_status"] == "pending_qc"                 # nothing changed


def test_a_rejected_document_cannot_be_split_again(db):
    add(db, "d1", [1, 2, 3])
    split_document(db, None, "u", "d1", 2)
    with pytest.raises(DocumentEditError, match="rejected"):
        split_document(db, None, "u", "d1", 3)
    assert why_not_editable(db.get_document("d1"))


def test_the_new_documents_stay_in_their_custody_bundle(db):
    db.insert_bundle("bn1", "p1", "Box 1", "DOK-BUNDLE-1") if hasattr(db, "insert_bundle") else None
    add(db, "d1", [1, 2])
    db.link_bundle_document("bn1", "d1", "b1")
    result = split_document(db, None, "u", "d1", 2)
    for new_id in result.new_ids:
        assert db.bundles_of_document(new_id) == ["bn1"]


def test_a_document_that_was_already_delivered_is_reported(db):
    add(db, "d1", [1, 2], status="auto_approved", delivered=True)
    result = split_document(db, None, "u", "d1", 2)
    assert result.already_delivered == ["d1"]


# -- merge --------------------------------------------------------------------------------------
def test_two_documents_are_merged_in_scan_order(db):
    add(db, "d1", [3, 4], fields={"barcode": "CIF-2", "email": "a@b.com"}, types=["Application", "Application"])
    add(db, "d2", [1, 2], fields={"barcode": "CIF-1", "nic_number": "553216789V"}, types=["NIC", "NIC"])
    audit = Audit()
    result = merge_documents(db, audit, "kamal", "d1", "d2")
    merged = db.get_document(result.new_ids[0])
    assert merged["page_range"] == "1-4" and merged["validation_status"] == "pending_qc"
    assert sorted(detail(db, result.new_ids[0])["pages"], key=int) == ["1", "2", "3", "4"]
    fields = json.loads(merged["extracted_fields"])
    assert fields["nic_number"] == "553216789V" and fields["email"] == "a@b.com"      # values of both
    assert fields["barcode"] == "CIF-1"                                               # the earlier page wins
    assert fields["doc_types"] == "NIC, Application"
    assert db.get_document("d1")["validation_status"] == db.get_document("d2")["validation_status"] == "rejected"
    assert audit.events[0][0] == "document_merged"


def test_documents_of_different_batches_or_the_same_document_cannot_be_merged(db):
    add(db, "d1", [1, 2])
    add(db, "d2", [3, 4], batch="b2")
    with pytest.raises(DocumentEditError, match="same batch"):
        merge_documents(db, None, "u", "d1", "d2")
    with pytest.raises(DocumentEditError, match="different"):
        merge_documents(db, None, "u", "d1", "d1")


# -- the dialog -----------------------------------------------------------------------------------
def test_the_dialog_splits_at_the_selected_page_and_lists_merge_partners(qtbot, db, monkeypatch):
    from ui.split_merge_dialog import SplitMergeDialog
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    add(db, "d1", [1, 2, 3])
    add(db, "d2", [4, 5])
    dlg = SplitMergeDialog(db, None, "kamal", "d1")
    qtbot.addWidget(dlg)
    assert dlg.pages_list.count() == 3 and dlg.merge_combo.count() == 1
    assert not dlg.split_btn.isEnabled()                         # nothing chosen yet
    dlg.pages_list.setCurrentRow(0)
    assert not dlg.split_btn.isEnabled()                         # the first page cannot start a second document
    dlg.pages_list.setCurrentRow(2)
    assert dlg.split_btn.isEnabled()
    dlg.split_btn.click()
    assert dlg.result_edit is not None and len(dlg.result_edit.new_ids) == 2


def test_the_dialog_merges_with_the_chosen_document(qtbot, db, monkeypatch):
    from ui.split_merge_dialog import SplitMergeDialog
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    add(db, "d1", [1, 2])
    add(db, "d2", [3])
    dlg = SplitMergeDialog(db, None, "kamal", "d1")
    qtbot.addWidget(dlg)
    dlg.merge_btn.click()
    assert dlg.result_edit.superseded == ["d1", "d2"]


def test_the_dialog_refuses_a_rejected_document(qtbot, db):
    from ui.split_merge_dialog import SplitMergeDialog
    add(db, "d1", [1, 2], status="rejected")
    dlg = SplitMergeDialog(db, None, "kamal", "d1")
    qtbot.addWidget(dlg)
    assert not dlg.split_btn.isEnabled() and not dlg.merge_btn.isEnabled()


def test_qc_review_has_the_split_merge_button_for_reviewers_only(qtbot, db):
    from security.rbac import User
    from ui.qc_review import QCReviewPanel
    reviewer = QCReviewPanel(db, None, User("u", "qc", "qc_officer"))
    viewer = QCReviewPanel(db, None, User("v", "view", "viewer"))
    for p in (reviewer, viewer):
        qtbot.addWidget(p)
    assert reviewer.split_merge_btn.isEnabled() and not viewer.split_merge_btn.isEnabled()
