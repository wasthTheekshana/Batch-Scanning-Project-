"""The Commercial Bank education loan application, read by region.

Uses the real form (docs/education-loan-english.pdf, dropped in by the user and
not part of the repository): skipped when it is not there."""
import sys
from pathlib import Path

import fitz
import numpy as np
import pytest

from ocr.doc_types import page_zone_groups
from ocr.tesseract_engine import ocr_page
from ocr.zone_extractor import extract_fields

ROOT = Path(__file__).parent.parent
FORM = ROOT / "docs" / "education-loan-english.pdf"
sys.path.insert(0, str(ROOT / "scripts"))
import loan_form  # noqa: E402

pytestmark = pytest.mark.skipif(not FORM.exists(),
                                reason="the sample bank form is not in docs/")


def render(pdf_path, dpi=200):
    with fitz.open(str(pdf_path)) as doc:
        pix = doc[0].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
        return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)


@pytest.fixture(scope="module")
def filled(tmp_path_factory):
    return loan_form.fill_first_page(FORM, tmp_path_factory.mktemp("form") / "f.pdf")


@pytest.fixture(scope="module", params=[150, 200, 300])
def read(request, filled):
    """The same filled form scanned at three resolutions (the pipeline imports
    PDFs at 300 dpi, scanners usually deliver 200-300)."""
    return extract_fields(render(filled, request.param), loan_form.zones())


@pytest.mark.parametrize("name", [f[0] for f in loan_form.FIELDS])
def test_every_applicant_field_is_read_exactly(read, name):
    assert read[name].value == loan_form.SAMPLE[name]


def test_a_different_applicants_entries_are_read_just_as_exactly(tmp_path):
    values = {"account_number": "0987654321", "branch": "Kandy City Centre",
              "applicant_name": "KAMALA SILVA", "date_of_birth": "31121975",
              "mobile": "0719876543", "email": "k.silva@example.com",
              "nic_number": "756789012V", "passport_number": "N7654321",
              "address": "14 Lake Road, Kandy"}
    filled_other = loan_form.fill_first_page(FORM, tmp_path / "other.pdf", values)
    got = extract_fields(render(filled_other), loan_form.zones())
    assert {k: got[k].value for k in values} == values


def test_boxed_fields_are_read_with_enough_confidence_not_to_need_review(read):
    for name in ("account_number", "date_of_birth", "nic_number", "passport_number"):
        assert read[name].confidence >= 0.75, name


def test_the_page_is_recognised_as_this_form_from_its_title(filled):
    import json
    ocr_cfg = json.loads((ROOT / "core" / "templates"
                          / "bank_customer_file.json").read_text(
        encoding="utf-8"))["config"]["ocr"]
    groups, name = page_zone_groups(ocr_cfg, ocr_page(render(filled)).text)
    assert name == "Education loan application"
    assert [mode for _zones, mode in groups] == ["zone"]
    assert [z["name"] for z in groups[0][0]] == [f[0] for f in loan_form.FIELDS]


def test_the_templates_form_zones_are_the_ones_tested_here():
    import json
    template = json.loads((ROOT / "core" / "templates"
                           / "bank_customer_file.json").read_text(encoding="utf-8"))
    form = template["config"]["ocr"]["document_types"][0]
    assert form == loan_form.document_type()


def test_only_the_first_page_carries_the_form_title():
    with fitz.open(str(FORM)) as doc:
        import re
        hits = [bool(re.search(loan_form.TITLE_PATTERN, page.get_text(), re.I))
                for page in doc]
    assert hits == [True, False, False, False]
