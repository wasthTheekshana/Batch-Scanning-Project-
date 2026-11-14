"""A stack with no barcodes is cut into documents at each form's title page."""
from pipeline.job_queue import _starts_document
from separation.splitter import split_documents

CFG = {"ocr": {"document_types": [
    {"name": "Loan", "regex": "education loan application", "starts_document": True,
     "continuation_regex": "declaration"},
    {"name": "NIC", "regex": "identity card"}]}}


def page(n, start=False, **kw):
    return {"page_no": n, "image": None, "is_blank": False, "barcodes": [],
            "ocr_text": "", "starts_document": start, **kw}


def sizes(docs):
    return [[p["page_no"] for p in d["pages"]] for d in docs]


def test_each_title_page_begins_a_document():
    pages = [page(1, True), page(2), page(3), page(4, True), page(5)]
    docs = split_documents(pages, {"strategy": ["document_type"]})
    assert sizes(docs) == [[1, 2, 3], [4, 5]]


def test_pages_before_the_first_title_form_their_own_document():
    docs = split_documents([page(1), page(2, True), page(3)], {"strategy": ["document_type"]})
    assert sizes(docs) == [[1], [2, 3]]


def test_without_the_strategy_title_pages_do_not_split():
    docs = split_documents([page(1, True), page(2, True)], {"strategy": ["barcode"]})
    assert sizes(docs) == [[1, 2]]


def test_it_combines_with_barcode_splitting():
    pages = [page(1, barcodes=[{"value": "CIF-1"}]), page(2), page(3, True), page(4)]
    docs = split_documents(pages, {"strategy": ["barcode", "document_type"]})
    assert sizes(docs) == [[1, 2], [3, 4]] and docs[0]["barcode"] == "CIF-1"


def test_only_a_marked_title_page_counts():
    assert _starts_document("Education Loan Application form", CFG["ocr"])
    assert not _starts_document("page 2 declaration", CFG["ocr"])      # later page
    assert not _starts_document("National identity card", CFG["ocr"])  # type not marked
    assert not _starts_document("nothing here", CFG["ocr"])


def test_the_dialog_remembers_the_flag(qtbot):
    from ui.document_type_dialog import DocumentTypeDialog
    dlg = DocumentTypeDialog(CFG["ocr"]["document_types"][0])
    qtbot.addWidget(dlg)
    assert dlg.starts_document.isChecked()
    assert dlg.build_type()["starts_document"] is True
    dlg.starts_document.setChecked(False)
    assert "starts_document" not in dlg.build_type()


# -- through the real pipeline -------------------------------------------------------
def test_the_pipeline_cuts_a_barcodeless_stack_at_each_title_page(tmp_path):

    from pipeline.job_queue import Pipeline
    from data.audit import AuditLogger
    from data.db import Database
    from delivery.local_folder import LocalFolderChannel
    from export.batch_exporter import export_document
    from ocr.tesseract_engine import OCRResult
    from tests.test_pipeline import make_profile, wait_for, write_pages

    texts = iter(["Education Loan Application No: A1", "page two declaration",
                  "Education Loan Application No: B2", "page two declaration",
                  "page three declaration"])
    db = Database(tmp_path / "sp.db")
    db.insert_project("proj-1", "Test Project", {})
    audit = AuditLogger(db, tmp_path / "logs")
    profile = make_profile(tmp_path)
    profile["separation"] = {"strategy": ["document_type"], "blank_threshold": 0.02}
    profile["features"]["auto_index"] = True
    profile["ocr"] = {"lang": "eng", "mode": "full_page", "confidence_threshold": 0.5,
                      "zones": [], "document_types": [
                          {"name": "Loan", "regex": "education loan application",
                           "continuation_regex": "declaration", "starts_document": True,
                           "zones": [{"name": "app_no", "regex": r"No:\s*(\S+)"}]}]}
    pipeline = Pipeline(db, profile, [LocalFolderChannel(profile["delivery"]["local_folder"])],
                        export_document, audit=audit, working_dir=tmp_path / "staging",
                        ocr_fn=lambda image: OCRResult(next(texts), 0.95))
    jobs = write_pages(tmp_path, ["text"] * 5)
    pipeline.start()
    try:
        pipeline.submit_batch("batch-1", jobs, operator="admin")
        assert wait_for(lambda: len(db.list_documents("batch-1")) == 2)
        pages = sorted(d["page_range"] for d in db.list_documents("batch-1"))
        assert pages == ["1-2", "3-5"]
    finally:
        pipeline.shutdown()
        audit.close()
        db.close()
