"""An operator corrects a page's document type: the stored types, the index
fields, the exported PDF's bookmarks and the delivered copies all follow."""
import json
import shutil

import fitz
import pytest

from core.job_queue import Pipeline
from core.rbac import Session, hash_password
from data.db import Database
from delivery.local_folder import LocalFolderChannel
from export.batch_exporter import export_document, set_type_bookmarks
from tests.test_pipeline import make_profile, wait_for

OCR_CFG = {"document_types": [{"name": "NIC", "regex": "identity", "zones": [{"name": "n", "regex": "x"}]},
                              {"name": "Application", "regex": "application",
                               "zones": [{"name": "a", "regex": "x"}]}]}


def make_pdf(path, pages=3):
    path.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open() as doc:
        for _ in range(pages):
            doc.new_page()
        doc.save(str(path))
    return path


def toc(path):
    with fitz.open(str(path)) as doc:
        return [(title, page) for _lvl, title, page in doc.get_toc()]


# -- the bookmarks ----------------------------------------------------------------------------
def test_bookmarks_can_be_replaced_and_cleared_after_export(tmp_path):
    pdf = make_pdf(tmp_path / "a.pdf")
    set_type_bookmarks(pdf, ["NIC", "NIC", "Application"])
    assert toc(pdf) == [("NIC", 1), ("Application", 3)]
    set_type_bookmarks(pdf, ["Application", "Application", "Application"])
    assert toc(pdf) == [("Application", 1)]
    set_type_bookmarks(pdf, [None, None, None])
    assert toc(pdf) == []                                   # no type left: no bookmark


# -- the Index tab -----------------------------------------------------------------------------------
@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    d.insert_project("p1", "P", {})
    d.insert_batch("b1", "p1", "admin")
    yield d
    Session.logout()
    d.close()


def add_doc(db, tmp_path, types=("NIC", "NIC", "Application")):
    pdf = make_pdf(tmp_path / "exported" / "doc.pdf", len(types))
    set_type_bookmarks(pdf, list(types))
    pages = {str(i + 1): {"doc_type": t} if t else {} for i, t in enumerate(types)}
    db.insert_document("d1", "b1", "p1", page_range="1-3", qc_score=90,
                       validation_status="auto_approved",
                       extracted_fields={"barcode": "CIF-1", "doc_types": "NIC, Application"},
                       qc_detail={"pages": pages})
    db.update_document("d1", storage_path=str(pdf))
    return pdf


def editor(qtbot, db, role="admin"):
    from ui.index_editor import IndexEditor
    db.insert_user(f"u-{role}", f"{role}1", hash_password("pw"), role)
    user = Session.login(f"{role}1", "pw", db)
    ed = IndexEditor(db, [{"name": "barcode"}], None, user,
                     zones_for_project=lambda pid: ([{"name": "barcode"}], False),
                     ocr_cfg_for_project=lambda pid: OCR_CFG)
    qtbot.addWidget(ed)
    ed.load_document("d1")
    return ed


def test_each_page_gets_a_drop_down_with_the_projects_types(qtbot, db, tmp_path):
    add_doc(db, tmp_path)
    ed = editor(qtbot, db)
    assert not ed.types_box.isHidden() and list(ed._type_combos) == ["1", "2", "3"]
    combo = ed._type_combos["3"]
    assert [combo.itemText(i) for i in range(combo.count())] == ["(no type)", "NIC", "Application"]
    assert [ed._type_combos[p].currentData() for p in "123"] == ["NIC", "NIC", "Application"]


def test_saving_stores_the_types_updates_the_index_field_and_signals(qtbot, db, tmp_path):
    add_doc(db, tmp_path)
    ed = editor(qtbot, db)
    ed._type_combos["2"].setCurrentIndex(ed._type_combos["2"].findData("Application"))
    with qtbot.waitSignal(ed.page_types_saved) as sig:
        ed.save_page_types()
    assert sig.args == ["d1"]
    row = db.get_document("d1")
    pages = json.loads(row["qc_detail"])["pages"]
    assert [pages[p].get("doc_type") for p in "123"] == ["NIC", "Application", "Application"]
    assert json.loads(row["extracted_fields"])["doc_types"] == "NIC, Application"
    ed._type_combos["1"].setCurrentIndex(0)
    ed._type_combos["2"].setCurrentIndex(0)
    ed._type_combos["3"].setCurrentIndex(0)
    ed.save_page_types()
    assert "doc_types" not in json.loads(db.get_document("d1")["extracted_fields"])


def test_saving_without_a_change_does_nothing(qtbot, db, tmp_path):
    add_doc(db, tmp_path)
    ed = editor(qtbot, db)
    emitted = []
    ed.page_types_saved.connect(emitted.append)
    ed.save_page_types()
    assert emitted == [] and "No page type was changed" in ed.hint.text()


def test_a_viewer_cannot_change_page_types(qtbot, db, tmp_path):
    add_doc(db, tmp_path)
    ed = editor(qtbot, db, "viewer")
    assert not ed.save_types_btn.isEnabled() and not ed._type_combos["1"].isEnabled()
    emitted = []
    ed.page_types_saved.connect(emitted.append)
    ed._type_combos["1"].setCurrentIndex(0)
    ed.save_page_types()
    assert emitted == []


def test_a_project_without_document_types_shows_no_section(qtbot, db, tmp_path):
    add_doc(db, tmp_path)
    from ui.index_editor import IndexEditor
    db.insert_user("u-a", "a1", hash_password("pw"), "admin")
    ed = IndexEditor(db, [{"name": "barcode"}], None, Session.login("a1", "pw", db),
                     zones_for_project=lambda pid: ([{"name": "barcode"}], False),
                     ocr_cfg_for_project=lambda pid: {})
    qtbot.addWidget(ed)
    ed.load_document("d1")
    assert ed.types_box.isHidden()


# -- the pipeline ----------------------------------------------------------------------------------------
def pipeline_with_delivery(tmp_path, db):
    profile = make_profile(tmp_path)
    profile["export"]["index"] = {"enabled": True, "format": "csv"}
    channel = LocalFolderChannel(profile["delivery"]["local_folder"])
    p = Pipeline(db, profile, [channel], export_document, working_dir=tmp_path / "staging")
    return p, profile


def test_a_correction_rewrites_the_bookmarks_and_redelivers_pdf_and_index(tmp_path, db):
    pdf = add_doc(db, tmp_path)
    pipe, profile = pipeline_with_delivery(tmp_path, db)
    delivered_dir = tmp_path / "delivered"
    delivered_dir.mkdir(exist_ok=True)
    delivered = delivered_dir / "doc.pdf"
    shutil.copy2(pdf, delivered)
    db.update_document("d1", delivery_status="delivered_local",
                       delivery_detail={"local_folder": {"success": True, "checksum": "old"}})
    # the operator's correction, as the Index tab stores it
    detail = json.loads(db.get_document("d1")["qc_detail"])
    detail["pages"]["2"]["doc_type"] = "Application"
    db.update_document("d1", qc_detail=detail,
                       extracted_fields={"barcode": "CIF-1", "doc_types": "NIC, Application"})
    pipe.start()
    try:
        assert pipe.reissue_page_types("d1") is True
        assert wait_for(lambda: toc(pdf) == [("NIC", 1), ("Application", 2)])
        assert wait_for(lambda: toc(delivered) == [("NIC", 1), ("Application", 2)])
        assert wait_for(lambda: pdf.with_suffix(".csv").exists())
    finally:
        pipe.shutdown()
    assert json.loads(db.get_document("d1")["delivery_detail"])["local_folder"]["checksum"] != "old"
    assert "NIC, Application" in pdf.with_suffix(".csv").read_text(encoding="utf-8-sig")


def test_no_reissue_for_a_document_that_was_never_exported(tmp_path, db):
    db.insert_document("d2", "b1", "p1", page_range="1-1", qc_score=90,
                       validation_status="pending_qc", extracted_fields={})
    pipe, _p = pipeline_with_delivery(tmp_path, db)
    assert pipe.reissue_page_types("d2") is False
    assert pipe.reissue_page_types("nope") is False


# -- the queue of uncertain fields ---------------------------------------------------------------------
def test_the_index_queue_can_show_only_documents_with_fields_to_check(qtbot, db, tmp_path):
    add_doc(db, tmp_path)
    db.insert_document("d2", "b1", "p1", page_range="4-4", qc_score=90,
                       validation_status="auto_approved", extracted_fields={"barcode": "CIF-2"},
                       qc_detail={"pages": {}, "low_conf_fields": ["barcode", "nic_number"]})
    ed = editor(qtbot, db)
    ed.filter_combo.setCurrentIndex(ed.filter_combo.findData("lowconf"))
    items = [ed.doc_list.item(i).text() for i in range(ed.doc_list.count())]
    assert len(items) == 1 and items[0].startswith("d2") and "2 field(s) to check" in items[0]
    ed.filter_combo.setCurrentIndex(ed.filter_combo.findData("all"))
    texts = [ed.doc_list.item(i).text() for i in range(ed.doc_list.count())]
    assert len(texts) == 2 and sum("to check" in t for t in texts) == 1


def test_saving_the_fields_takes_a_document_out_of_the_to_check_queue(qtbot, db, tmp_path):
    db.insert_document("d1", "b1", "p1", page_range="1-1", qc_score=90,
                       validation_status="auto_approved", extracted_fields={"barcode": "CIF-2"},
                       qc_detail={"pages": {}, "low_conf_fields": ["barcode"], "flags": ["low_confidence_index"]})
    ed = editor(qtbot, db)
    ed.filter_combo.setCurrentIndex(ed.filter_combo.findData("lowconf"))
    assert ed.doc_list.count() == 1
    ed.doc_list.setCurrentRow(0)
    ed.save()
    ed.refresh()
    assert ed.doc_list.count() == 0
