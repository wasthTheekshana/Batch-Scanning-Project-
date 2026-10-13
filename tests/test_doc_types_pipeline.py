"""Document types through the real pipeline: classification per page,
per-type fields, and bookmarks in the exported PDF."""
import json

import cv2
import fitz
import pytest

from core.job_queue import Pipeline
from data.db import Database
from delivery.local_folder import LocalFolderChannel
from export.batch_exporter import export_document
from ocr.tesseract_engine import OCRResult
from tests.test_pipeline import make_profile, wait_for, write_pages

TEXT = {
    2: "NATIONAL IDENTITY CARD Name: NIMAL PERERA ID No: 199012345678",
    3: "Address: 25 Temple Road, Colombo 07",
    4: "PERSONAL LOAN APPLICATION FORM Application No: APP-55001",
    5: "Signature: ______",
    6: "Salary letter, no known type",
}

OCR_CFG = {
    "lang": "eng", "mode": "full_page", "confidence_threshold": 0.75,
    "zones": [],
    "document_types": [
        {"name": "NIC", "regex": "national identity card",
         "zones": [{"name": "nic_number",
                    "regex": r"ID No:\s*(\d{9}[VvXx]|\d{12})"}]},
        {"name": "Application", "regex": r"application\s+form",
         "zones": [{"name": "application_number",
                    "regex": r"Application No:\s*(APP-\d+)"}]}],
}


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "dt.db")
    d.insert_project("proj-1", "Test Project", {})
    yield d
    d.close()


def run(tmp_path, db, monkeypatch, ocr_cfg=OCR_CFG, pages=(2, 3, 4, 5, 6),
        auto_index=True):
    import core.job_queue as jq
    profile = make_profile(tmp_path)
    profile["separation"] = {"strategy": ["barcode"], "barcode_role": "cover_sheet",
                             "blank_threshold": 0.02}
    profile["ocr"] = ocr_cfg
    profile["features"]["auto_index"] = auto_index
    profile["export"]["format"] = "image_pdf"
    pipeline = Pipeline(
        db, profile, [LocalFolderChannel(profile["delivery"]["local_folder"])],
        export_document, working_dir=tmp_path / "staging",
        ocr_fn=lambda image: OCRResult(text=TEXT.get(int(image[5, 5]) - 100, ""),
                                       confidence=0.95))
    jobs = write_pages(tmp_path, ["text"] * (len(pages) + 1))
    for j in jobs:
        j.batch_id = "batch-1"
        img = cv2.imread(j.image_path, cv2.IMREAD_UNCHANGED)
        img[5, 5] = 100 + j.page_no
        cv2.imwrite(j.image_path, img)
    monkeypatch.setattr(jq, "decode_barcodes", lambda image: (
        [{"type": "CODE128", "value": "CIF-1", "rect": (0, 0, 9, 9)}]
        if int(image[5, 5]) - 100 == 1 else []))
    pipeline.start()
    try:
        pipeline.submit_batch("batch-1", jobs)
        assert wait_for(lambda: any(d["storage_path"]
                                    for d in db.list_documents("batch-1")))
    finally:
        pipeline.shutdown()
    return db.list_documents("batch-1")[0]


def test_each_page_is_typed_and_untyped_pages_follow_the_page_before(tmp_path, db, monkeypatch):
    doc = run(tmp_path, db, monkeypatch)
    pages = json.loads(doc["qc_detail"])["pages"]
    assert {no: info.get("doc_type") for no, info in pages.items()} == {
        "2": "NIC", "3": "NIC", "4": "Application", "5": "Application",
        "6": "Application"}


def test_fields_come_from_the_type_of_the_page_they_are_on(tmp_path, db, monkeypatch):
    fields = json.loads(run(tmp_path, db, monkeypatch)["extracted_fields"])
    assert fields["nic_number"] == "199012345678"
    assert fields["application_number"] == "APP-55001"
    assert fields["barcode"] == "CIF-1"
    assert fields["doc_types"] == "NIC, Application"


def test_a_types_fields_are_not_looked_for_on_other_types_pages(tmp_path, db, monkeypatch):
    """Page 4 is an application: the NIC zone must not run on it (and the
    other way round), so each field has exactly one source page."""
    cfg = json.loads(json.dumps(OCR_CFG))
    cfg["document_types"][0]["zones"][0]["regex"] = r"(APP-\d+)"   # NIC zone
    fields = json.loads(run(tmp_path, db, monkeypatch, cfg)["extracted_fields"])
    assert "nic_number" not in fields or not fields["nic_number"]


def test_the_exported_pdf_has_a_bookmark_per_document_type(tmp_path, db, monkeypatch):
    doc = run(tmp_path, db, monkeypatch)
    with fitz.open(doc["storage_path"]) as pdf:
        assert pdf.page_count == 5                  # the cover sheet is not in it
        assert pdf.get_toc() == [[1, "NIC", 1], [1, "Application", 3]]


def test_a_project_without_document_types_exports_without_bookmarks(tmp_path, db, monkeypatch):
    cfg = {"lang": "eng", "mode": "full_page", "confidence_threshold": 0.75,
           "zones": [{"name": "nic_number", "regex": r"ID No:\s*(\d{12})"}]}
    doc = run(tmp_path, db, monkeypatch, cfg)
    assert json.loads(doc["extracted_fields"])["nic_number"] == "199012345678"
    assert "doc_types" not in json.loads(doc["extracted_fields"])
    with fitz.open(doc["storage_path"]) as pdf:
        assert pdf.get_toc() == []


def test_types_label_pages_even_when_auto_index_is_off(tmp_path, db, monkeypatch):
    doc = run(tmp_path, db, monkeypatch, auto_index=False)
    fields = json.loads(doc["extracted_fields"])
    assert "nic_number" not in fields               # no extraction ...
    assert fields["doc_types"] == "NIC, Application"   # ... but pages typed


def test_a_type_can_read_regions_while_the_rest_of_the_project_reads_text(tmp_path, db, monkeypatch):
    """A boxed bank form is read by region, the ID card by text pattern, in
    one customer file: the form's zone is OCR'd as a crop, the NIC's as text."""
    cfg = json.loads(json.dumps(OCR_CFG))
    cfg["document_types"][1] = {          # Application: boxes on the page
        "name": "Application", "regex": r"application\s+form", "mode": "zone",
        "zones": [{"name": "account_number", "x": 10, "y": 10, "w": 200, "h": 40}]}

    def ocr(image):
        if image.shape[0] < 500:                       # a cropped region
            return OCRResult(text="0012345678", confidence=0.95)
        return OCRResult(text=TEXT.get(int(image[5, 5]) - 100, ""), confidence=0.95)

    import core.job_queue as jq
    profile = make_profile(tmp_path)
    profile["separation"] = {"strategy": ["barcode"], "barcode_role": "cover_sheet",
                             "blank_threshold": 0.02}
    profile["ocr"] = cfg
    profile["features"]["auto_index"] = True
    profile["export"]["format"] = "image_pdf"
    pipeline = Pipeline(
        db, profile, [LocalFolderChannel(profile["delivery"]["local_folder"])],
        export_document, working_dir=tmp_path / "staging", ocr_fn=ocr)
    jobs = write_pages(tmp_path, ["text"] * 6)
    for j in jobs:
        j.batch_id = "batch-1"
        img = cv2.imread(j.image_path, cv2.IMREAD_UNCHANGED)
        img[5, 5] = 100 + j.page_no
        cv2.imwrite(j.image_path, img)
    monkeypatch.setattr(jq, "decode_barcodes", lambda image: (
        [{"type": "CODE128", "value": "CIF-1", "rect": (0, 0, 9, 9)}]
        if int(image[5, 5]) - 100 == 1 else []))
    pipeline.start()
    try:
        pipeline.submit_batch("batch-1", jobs)
        assert wait_for(lambda: any(d["storage_path"]
                                    for d in db.list_documents("batch-1")))
    finally:
        pipeline.shutdown()
    fields = json.loads(db.list_documents("batch-1")[0]["extracted_fields"])
    assert fields["nic_number"] == "199012345678"       # from the page text
    assert fields["account_number"] == "0012345678"     # from the cropped region
