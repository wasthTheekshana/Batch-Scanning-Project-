"""Correcting what the handwriting reader returns, and the pieces around it."""
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from ocr import comb, fieldfix, handwriting
from ocr.doc_types import match_page


# -- e-mail ------------------------------------------------------------------------------------
@pytest.mark.parametrize("read, want", [
    ("abc@gmail.com", "abc@gmail.com"),
    ("abc @ gmail , com", "abc@gmail.com"),
    ("ABC@Gmail.com", "abc@gmail.com"),
    ("kamal@gmial.com", "kamal@gmail.com"),            # the domain is nearly a real one
])
def test_an_email_is_repaired(read, want):
    assert fieldfix.fix_email(read)[0] == want


def test_a_lost_at_sign_is_put_back_before_a_known_domain():
    value, confidence = fieldfix.fix_email("abcgmail.com")
    assert value == "abc@gmail.com" and confidence < 1.0          # repaired, so worth a look


def test_email_candidates_drop_the_letters_an_at_sign_was_read_as():
    candidates = fieldfix.email_candidates("abclegmcii-com-")
    assert "abc@gmail.com" in candidates
    assert fieldfix.email_prior("abc@gmail.com") > fieldfix.email_prior("a_b@mail.com")


# -- mobile and NIC --------------------------------------------------------------------------------
def test_a_mobile_number_is_digits_with_its_leading_zero():
    assert fieldfix.fix_mobile("076 900 4460") == ("0769004460", 1.0)
    assert fieldfix.fix_mobile("O769O04460")[0] == "0769004460"            # letters that are digits
    assert fieldfix.fix_mobile("769004460")[0] == "0769004460"             # dropped zero
    assert fieldfix.fix_mobile("+94769004460")[0] == "0769004460"
    assert fieldfix.fix_mobile("0769")[1] < 1.0                           # too short: flagged


def test_an_nic_is_nine_digits_and_a_letter_or_twelve_digits():
    assert fieldfix.fix_nic("553216789v") == ("553216789V", 1.0)
    assert fieldfix.fix_nic("199012345678") == ("199012345678", 1.0)
    value, confidence = fieldfix.fix_nic("553216789")
    assert value == "553216789V" and confidence < 1.0


# -- names ----------------------------------------------------------------------------------------------
def test_a_name_loses_box_edge_marks_and_gets_capitals():
    assert fieldfix.fix_name("I tharindu  perera .", snap=False)[0] == "Tharindu Perera"
    assert fieldfix.fix_name("kamal D'Silva", snap=False)[0] == "Kamal D'Silva"


def test_a_name_near_a_known_one_can_be_snapped_to_it():
    assert any("Kalindu" in v for v in fieldfix.name_variants("Kaindu Perera"))
    assert fieldfix.name_prior("Kalindu Perera") > fieldfix.name_prior("Zzxqw Perera")


# -- branch --------------------------------------------------------------------------------------------------
def test_a_branch_is_the_nearest_branch_of_the_list():
    assert fieldfix.fix_branch("Matera")[0] == "Matara"
    assert fieldfix.fix_branch("Jafna")[0] == "Jaffna"
    value, confidence = fieldfix.fix_branch("Zzzzzz")
    assert value == "Zzzzzz" and confidence < 0.5                  # not a branch: kept as read, doubtful


def test_the_models_ranking_of_branches_is_used_when_given():
    value, confidence = fieldfix.fix_branch("x", [("Galle", -0.4), ("Matara", -2.5)])
    assert value == "Galle" and confidence > 0.5


# -- reading without the model -------------------------------------------------------------------------------
def test_without_the_model_files_nothing_is_read_and_nothing_breaks(monkeypatch, tmp_path):
    monkeypatch.setenv("DOK_TROCR_DIR", str(tmp_path))
    assert not handwriting.available()
    assert handwriting.read_line(np.full((40, 200, 3), 255, np.uint8)) == ("", 0.0)
    assert handwriting.rank(np.full((40, 200, 3), 255, np.uint8), ["a"]) == []


def test_clean_lines_keeps_the_writing_and_drops_the_box_rules():
    page = np.full((120, 600, 3), 255, np.uint8)
    cv2.rectangle(page, (5, 20), (590, 100), (40, 40, 40), 2)               # the printed box
    cv2.putText(page, "Perera", (30, 75), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (150, 40, 20), 3)   # blue pen
    lines = handwriting.clean_lines(page)
    assert len(lines) == 1
    dark = (cv2.cvtColor(lines[0], cv2.COLOR_BGR2GRAY) < 128).mean()
    assert 0.01 < dark < 0.35                                                # strokes, not a black box


# -- boxes ------------------------------------------------------------------------------------------------------------
def _row(cells, hide=()):
    img = np.full((60, cells * 55 + 1), 255, np.uint8)
    for i in range(cells + 1):
        if i not in hide:
            cv2.line(img, (i * 55, 0), (i * 55, img.shape[0] - 1), 60, 2)
    return img


def test_a_box_divider_hidden_by_a_pen_stroke_is_put_back():
    img = _row(12, hide={5})
    cv2.rectangle(img, (0, 0), (img.shape[1] - 1, img.shape[0] - 1), 60, 2)
    for i, ch in enumerate("534210056718"):
        cv2.putText(img, ch, (i * 55 + 12, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.2, 0, 2)
    assert len(comb.split_cells(img)) == 12


def test_blue_pen_is_separated_from_the_black_box_lines_by_colour():
    strip = np.full((60, 3 * 55 + 1, 3), 255, np.uint8)
    for i in range(4):
        cv2.line(strip, (i * 55, 0), (i * 55, 59), (50, 50, 50), 2)
    cv2.line(strip, (0, 2), (3 * 55, 2), (50, 50, 50), 2)
    cv2.putText(strip, "5", (14, 46), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (200, 60, 20), 3)   # BGR: blue
    gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
    pen = comb._pen_ink(strip, gray)
    assert pen is not None and pen[:, :55].any() and not pen[:, 60:].any()
    assert comb._pen_ink(np.full_like(strip, 255), gray) is None            # no coloured writing: grey way


def test_an_nic_of_ten_boxes_ends_in_a_letter(monkeypatch):
    seen = []
    monkeypatch.setattr(comb, "_read_char", lambda cell, allowed: (seen.append(allowed) or "5", 0.9))
    monkeypatch.setattr(comb, "cell_variants", lambda strip: [[np.ones((10, 10), np.uint8)]] * 10)
    comb.read_comb(np.zeros((10, 10)), "nic")
    assert seen[-1] == "VX" and set(seen[0]) == set("0123456789")


# -- which form a page is -----------------------------------------------------------------------------------------------
def _cfg():
    path = Path(__file__).parent.parent / "projects" / "templates" / "bank_customer_file.json"
    return json.loads(path.read_text(encoding="utf-8"))["config"]["ocr"]


def test_the_credit_card_form_is_not_taken_for_the_education_loan():
    page = "CREDIT CARD APPLICATION Personal Details Supplementary Card Supplementary Limit"
    doc_type, reads = match_page(page, _cfg())
    assert doc_type["name"] == "Credit card application" and reads
    later, reads = match_page("Authorised Officer's Declaration Details of a Relative", _cfg())
    assert later["name"] == "Credit card application" and not reads


def test_the_personal_loan_form_has_its_own_boxes():
    doc_type, reads = match_page("PERSONAL LOAN APPLICATION (SRI LANKAN RUPEE)", _cfg())
    assert doc_type["name"] == "Personal loan application" and reads
    names = {z["name"] for z in doc_type["zones"]}
    assert {"account_number", "applicant_name", "nic_number", "email", "mobile", "branch"} <= names
    later, reads = match_page("Loan Details Monthly Financial Details", _cfg())
    assert later["name"] == "Personal loan application" and not reads


def test_handwritten_zones_are_marked_for_the_handwriting_reader():
    doc_type, _ = match_page("PERSONAL LOAN APPLICATION", _cfg())
    kinds = {z["name"]: z.get("kind") for z in doc_type["zones"] if z.get("hand")}
    assert kinds["branch"] == "branch" and kinds["applicant_name"] == "name"
    assert kinds["email"] == "email" and kinds["mobile"] == "mobile"


# -- typed text must not be sent to the handwriting model ------------------------------------------------
def _zone():
    return {"name": "applicant_name", "x": 0, "y": 0, "w": 300, "h": 40, "lines": 1, "hand": True,
            "kind": "name", "regex": r"^[\s|.,;:_-]*([^|]+?)[\s|.,;:_-]*$"}


def _run(monkeypatch, typed_text, typed_conf):
    """Read one zone with a fake ordinary reader and a fake handwriting reader; which one decided?"""
    from ocr import zone_extractor as ze
    from ocr.tesseract_engine import OCRResult
    calls = []
    monkeypatch.setattr(ze, "ocr_page", lambda region, **kw: OCRResult(text=typed_text, confidence=typed_conf))
    monkeypatch.setattr(ze, "read_handwritten",
                        lambda strip, zone, hint="": calls.append("handwriting") or OCRResult(text="Written Name", confidence=0.6))
    got = ze.extract_fields(np.full((60, 400, 3), 255, np.uint8), [_zone()])["applicant_name"]
    return got, calls


def test_confidently_read_typed_text_is_kept_and_the_handwriting_model_is_not_asked(monkeypatch):
    got, calls = _run(monkeypatch, "| NIMAL KUMARA PERERA |", 0.88)
    assert got.value == "NIMAL KUMARA PERERA" and calls == []


def test_a_doubtful_reading_goes_to_the_handwriting_model(monkeypatch):
    got, calls = _run(monkeypatch, "IDV W Thumar | | pevera |", 0.77)         # what real handwriting gave
    assert calls == ["handwriting"] and got.value == "Written Name"


def test_an_empty_typed_reading_goes_to_the_handwriting_model(monkeypatch):
    got, calls = _run(monkeypatch, "| |", 0.95)
    assert calls == ["handwriting"]
