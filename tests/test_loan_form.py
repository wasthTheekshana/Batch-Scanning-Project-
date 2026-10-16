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

pytestmark = [pytest.mark.slow,
              pytest.mark.skipif(not FORM.exists(),
                                 reason="the sample bank form is not in docs/")]


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
    assert [z["name"] for z in groups[0][0]] == [f[0] for f in loan_form.FIELDS + loan_form.JOINT_FIELDS]


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


# -- a printed-and-scanned copy: smaller, shifted, slightly crooked -----------------------------
def distorted(filled, scale=0.95, angle=1.2, shift=(60, 45), dpi=200):
    """The filled form as a scan of a printed copy: shrunk by the printer's
    fit-to-page, offset by its margins and put in the scanner slightly crooked."""
    import cv2
    page = render(filled, dpi)
    h, w = page.shape
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, scale)
    matrix[:, 2] += shift
    return cv2.warpAffine(page, matrix, (w, h), borderValue=255)


@pytest.mark.parametrize("scale,angle,shift", [(0.95, 1.2, (60, 45)),
                                               (0.90, -0.8, (-30, 80)),
                                               (1.0, 0.0, (0, 0))])
def test_a_shrunk_shifted_crooked_copy_is_still_read_in_the_right_boxes(filled, scale, angle, shift):
    page = distorted(filled, scale, angle, shift)
    words = ocr_page(page).words
    zones = [{**z, "anchors": loan_form.anchors()} for z in loan_form.zones()]
    got = extract_fields(page, zones, words=words)
    exact = [name for name in loan_form.SAMPLE if got[name].value == loan_form.SAMPLE[name]]
    # the boxed numbers and the printed lines must all be right; a crooked page
    # may cost one free-text line
    assert len(exact) >= 8, {n: got[n].value for n in loan_form.SAMPLE if n not in exact}
    for name in ("account_number", "nic_number", "passport_number", "date_of_birth"):
        assert got[name].value == loan_form.SAMPLE[name], name


def test_without_alignment_the_same_shrunk_copy_is_read_in_the_wrong_boxes(filled):
    """What the alignment is for: the template positions alone miss."""
    page = distorted(filled, 0.90, 0.0, (-30, 80))
    got = extract_fields(page, loan_form.zones())          # no anchors, no words
    wrong = [n for n in loan_form.SAMPLE if got[n].value != loan_form.SAMPLE[n]]
    assert len(wrong) >= 4


def test_the_templates_form_type_carries_the_anchors():
    assert loan_form.document_type().get("anchors")


def test_a_straight_page_keeps_its_own_pixels_so_boxed_fields_read_exactly(filled):
    """Regression: resampling a page that needs no turning blurred the thin
    box lines and the date of birth was lost. A straight page is read in place."""
    from ocr.align import STRAIGHT_ENOUGH, estimate_transform, rotation_degrees
    page = distorted(filled, 0.97, 0.0, (40, 30), dpi=300)
    words = ocr_page(page).words
    matrix = estimate_transform(loan_form.anchors(), words)
    assert abs(rotation_degrees(matrix)) < STRAIGHT_ENOUGH
    zones = [{**z, "anchors": loan_form.anchors()} for z in loan_form.zones()]
    got = extract_fields(page, zones, words=words)
    assert got["date_of_birth"].value == loan_form.SAMPLE["date_of_birth"]
    assert got["branch"].value == loan_form.SAMPLE["branch"]


def test_a_blank_joint_column_reads_as_empty(filled):
    read = extract_fields(render(filled), loan_form.zones())
    junk = {n: read[n].value for n in read if n.startswith("joint_") and read[n].value}
    assert len([n for n in read if n.startswith("joint_")]) == 7
    assert len(junk) <= 2, junk          # box edges may leave stray marks, never a real value


def test_the_joint_applicants_column_is_read_too(tmp_path):
    values = {n: v for n, *_r, v in loan_form.JOINT_FIELDS}
    path = loan_form.fill_first_page(FORM, tmp_path / "joint.pdf", values)
    read = extract_fields(render(path), loan_form.zones())
    wrong = {n: read[n].value for n in values if read[n].value != values[n]}
    assert len(wrong) <= 1, wrong
    assert read["applicant_name"].value == loan_form.SAMPLE["applicant_name"]
