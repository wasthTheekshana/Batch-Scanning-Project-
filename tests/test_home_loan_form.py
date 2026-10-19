"""The Commercial Bank home loan application: its own zones and anchors, and the
box reader on pale or dotted box lines (found on a real Kodak S3060 scan)."""
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import home_loan_form as hl  # noqa: E402

from ocr import comb  # noqa: E402
from ocr.doc_types import match_page  # noqa: E402


def test_the_zones_are_the_applicants_and_the_joint_applicants():
    names = [z["name"] for z in hl.zones()]
    assert names[:9] == [n for n, *_ in hl.BOXES]
    joint = [n for n in names if n.startswith("joint_")]
    assert len(joint) == 7 and "joint_account_number" not in joint and "joint_branch" not in joint
    own = {z["name"]: z for z in hl.zones()}
    assert own["joint_nic_number"]["x"] == own["nic_number"]["x"] + hl.JOINT_OFFSET
    assert own["joint_nic_number"]["comb"] and own["nic_number"]["charset"] == "nic"


def test_every_zone_lies_inside_the_reference_page():
    for z in hl.zones():
        assert z["x"] >= 0 and z["y"] >= 0
        assert z["x"] + z["w"] <= z["ref_w"] and z["y"] + z["h"] <= z["ref_h"], z["name"]


def test_the_anchors_are_printed_words_only():
    anchors = hl.anchors()
    assert len(anchors) >= 40
    words = {a["t"] for a in anchors}
    assert {"account", "branch", "passport", "nationality"} <= words
    assert not words & {"single", "kottawa", "mawathgama"}          # handwriting
    for a in anchors:
        assert set(a) == {"t", "x", "y"}                            # words and places only


def test_the_title_picks_this_form_not_the_generic_application_type():
    import json
    ocr_cfg = json.loads((ROOT / "core" / "templates" / "bank_customer_file.json")
                         .read_text(encoding="utf-8"))["config"]["ocr"]
    doc_type, reads = match_page("COMMERCIAL BANK HOME LOAN APPLICATION (SRI LANKAN RUPEE)", ocr_cfg)
    assert doc_type["name"] == "Home loan application" and reads
    doc_type, _ = match_page("Education Loan Application", ocr_cfg)
    assert doc_type["name"] == "Education loan application"
    assert hl.document_type()["anchors"]


# -- pale box lines ---------------------------------------------------------------------------
def pale_row(cells=12, line=165, cell_w=58, height=59, digits="779100714568"):
    img = np.full((height, cells * cell_w + 1), 255, np.uint8)
    for i in range(cells + 1):
        cv2.line(img, (i * cell_w, 0), (i * cell_w, height - 1), line, 2)
    cv2.line(img, (0, 0), (cells * cell_w, 0), line, 2)
    cv2.line(img, (0, height - 1), (cells * cell_w, height - 1), line, 2)
    for i, d in enumerate(digits):
        cv2.putText(img, d, (i * cell_w + 14, 42), cv2.FONT_HERSHEY_SIMPLEX, 1.1, 0, 3)
    return img


def test_boxes_drawn_in_pale_grey_are_all_found():
    assert len(comb.split_cells(pale_row(line=165))) == 12
    assert len(comb.split_cells(pale_row(line=110))) == 12          # the dark case still works


def test_a_letter_can_only_close_a_nic_and_a_12_box_number_has_no_letter(monkeypatch):
    seen = []

    def fake(cell, whitelist):
        seen.append(whitelist)
        return "1", 0.9
    monkeypatch.setattr(comb, "_read_char", fake)
    comb.read_comb(pale_row(12), "nic")
    assert set(seen) == {comb.CHARSETS["digits"]}                   # 12 boxes: digits only
    seen.clear()
    comb.read_comb(pale_row(10, digits="856789012V"), "nic")
    assert seen.count(comb.CHARSETS["digits"]) == 9 and seen.count("VX") == 1      # nine digits, then V or X
    seen.clear()
    comb.read_comb(pale_row(11, digits="85678901234"), "nic")
    assert seen.count(comb.CHARSETS["nic"]) == 1
    seen.clear()
    comb.read_comb(pale_row(10, digits="8567890123"), "alnum")      # other charsets unchanged
    assert set(seen) == {comb.CHARSETS["alnum"]}


def test_a_second_page_is_taken_for_its_own_form_not_for_the_personal_loan():
    """The loan forms share "Loan Details" and "Monthly Financial Details"; only each form's own words may decide."""
    import json
    ocr_cfg = json.loads((ROOT / "core" / "templates" / "bank_customer_file.json")
                         .read_text(encoding="utf-8"))["config"]["ocr"]
    home = "Loan Details Loan Amount Purpose of the loan Purchase of land Purchase of house Construction / Renovation Monthly Financial Details"
    education = "Loan Details Required loan amount Course Details Study Programme Monthly Financial Details"
    personal = "Loan Amount Rs. Repayment Period Months Purpose of the loan Monthly Financial Details Primary Applicant"
    assert match_page(home, ocr_cfg)[0]["name"] == "Home loan application"
    assert match_page(education, ocr_cfg)[0]["name"] == "Education loan application"
    assert match_page(personal, ocr_cfg)[0]["name"] == "Personal loan application"
    assert match_page(home, ocr_cfg)[1] is False                    # a later page: typed, its fields are not read again
