"""The built-in "Bank - Customer file" template, run over realistic page text."""
import json
from pathlib import Path

import pytest

from core.profile_manager import ProfileManager
from core.template_manager import TemplateManager
from data.db import Database
from ocr.doc_types import all_zones, page_zones
from ocr.zone_extractor import extract_fields_from_text
from separation.splitter import split_documents

TEMPLATE = json.loads((Path(__file__).parent.parent / "core" / "templates"
                       / "bank_customer_file.json").read_text(encoding="utf-8"))
OCR = TEMPLATE["config"]["ocr"]


def read(text):
    zones, doc_type = page_zones(OCR, text)
    fields = {k: v.value for k, v in extract_fields_from_text(text, 0.9, zones).items()
              if v.value}
    return doc_type, fields


def test_new_style_nic_front():
    assert read("NATIONAL IDENTITY CARD Name: NIMAL PERERA ID No: 199012345678 "
                "Date of Birth: 12 Jan 1990") == ("NIC", {
        "nic_number": "199012345678", "nic_name": "NIMAL PERERA",
        "nic_date_of_birth": "12 Jan 1990"})


def test_old_style_nic_number_with_a_letter():
    doc_type, fields = read("National Identity Card Name: Kamala Silva "
                            "ID No: 856789012V Sex: Female")
    assert doc_type == "NIC"
    assert fields["nic_number"] == "856789012V"
    assert fields["nic_name"] == "Kamala Silva"


def test_the_back_of_the_nic_has_no_type_of_its_own():
    text = "Address: 25 Temple Road, Colombo 07 Date of Issue: 03 Feb 2015"
    assert read(text) == (None, {})


def test_loan_application_form():
    assert read("PERSONAL LOAN APPLICATION FORM Application No: APP-55001 "
                "Applicant Name: Nimal Perera Product: Personal Loan "
                "Requested Amount: LKR 500,000 Application Date: 14 Oct 2026") == (
        "Application", {"application_number": "APP-55001",
                        "applicant_name": "Nimal Perera",
                        "product": "Personal Loan",
                        "application_date": "14 Oct 2026"})


def test_an_application_that_mentions_a_nic_number_is_still_an_application():
    doc_type, fields = read("ACCOUNT OPENING APPLICATION FORM Applicant Name: "
                            "Kamala Silva NIC No: 856789012V Branch: Fort")
    assert doc_type == "Application"
    assert "nic_number" not in fields       # the NIC zones do not run here


def test_letter_fields():
    doc_type, fields = read("Date: 03 Mar 2026 Our Ref: HR/2026/118 Subject: "
                            "Confirmation of employment Dear Sir, this is to "
                            "confirm")
    assert doc_type == "Letter"
    assert fields["letter_reference"] == "HR/2026/118"
    assert fields["letter_date"] == "03 Mar 2026"
    assert fields["letter_subject"].startswith("Confirmation of employment")


def test_a_date_of_birth_is_not_taken_for_the_letter_date():
    doc_type, fields = read("Dear Sir, Date of Birth: 12 Jan 1990")
    assert doc_type == "Letter"
    assert "letter_date" not in fields


def test_field_names_are_unique_across_the_types():
    names = [z["name"] for t in OCR["document_types"] for z in t["zones"]]
    assert len(names) == len(set(names)) == len(all_zones(OCR))


# -- the template as a project -----------------------------------------------------------
@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


def test_the_template_is_seeded_and_builds_a_valid_project(tmp_path, db):
    manager = TemplateManager(db)
    manager.seed_built_ins()
    template = next(t for t in manager.list_templates()
                    if t["name"] == "Bank - Customer file")
    assert template["built_in"] and template["category"] == "Banking"
    cfg = manager.build_project_config(template["template_id"])
    created = ProfileManager(tmp_path / "projects", db,
                             data_root=tmp_path / "root").create(
        "Bank - Customer file", config=cfg)       # validates against the schema
    assert created["separation"]["barcode_role"] == "auto"
    assert [t["name"] for t in created["ocr"]["document_types"]] == [
        "Application", "NIC", "Letter"]
    assert created["export"]["filename_template"] == "{barcode}_{counter:04d}"
    assert created["export"]["output_path"].endswith("output")


def test_a_customers_pages_split_on_the_barcode_and_the_cover_is_dropped():
    def page(no, barcode=None, text=""):
        return {"page_no": no, "image": None, "is_blank": False, "ocr_text": text,
                "barcodes": ([{"type": "CODE128", "value": barcode,
                               "rect": (0, 0, 1, 1)}] if barcode else [])}
    docs = split_documents(
        [page(1, "CIF-1", "DOCUMENT COVER SHEET CIF-1"), page(2, text="NIC"),
         page(3, "CIF-2", "DOCUMENT COVER SHEET CIF-2"), page(4, text="APP")],
        TEMPLATE["config"]["separation"])
    assert [[p["page_no"] for p in d["pages"]] for d in docs] == [[2], [4]]
    assert [d["barcode"] for d in docs] == ["CIF-1", "CIF-2"]
