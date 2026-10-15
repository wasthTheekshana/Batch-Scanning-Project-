"""The Commercial Bank HOME LOAN APPLICATION (page 1), read by region.

A different revision of the same family of forms as the education loan
application (scripts/loan_form.py): the boxes sit 5-30 pt away from where the
education form has them, so it has its own zones. They were measured on a real
scan of the form (upright, 2488 x 3507 px = A4 at 300 dpi) and are in that
frame; the reader scales and aligns them to any scan through the anchors, the
printed words of the form (handwriting is left out of them).

The Applicant (left) column is read as the plain field names, the Joint
Applicant column as joint_<name> (1122 px to the right).
"""
from __future__ import annotations

import json
from pathlib import Path

import loan_form

REF = {"ref_w": 2488, "ref_h": 3507}
ANCHORS_FILE = Path(__file__).with_name("home_loan_anchors.json")
JOINT_OFFSET = 1122
TITLE_PATTERN = r"home\s+loan\s+application"

# name, x, y, w, h (px of the reference scan)
BOXES = [
    ("account_number", 458, 865, 595, 58),
    ("branch", 458, 946, 595, 50),
    ("applicant_name", 416, 1180, 792, 108),
    ("date_of_birth", 416, 1298, 525, 48),
    ("address", 178, 1728, 1028, 110),
    ("mobile", 322, 2014, 335, 49),
    ("email", 322, 2119, 885, 52),
    ("nic_number", 430, 2191, 713, 59),
    ("passport_number", 549, 2261, 594, 58),
]
_EXTRA = {f[0]: f[5] for f in loan_form.FIELDS}


def zones() -> list[dict]:
    own = [{"name": n, "x": x, "y": y, "w": w, "h": h, **REF, **_EXTRA[n]}
           for n, x, y, w, h in BOXES]
    joint = [{**z, "name": "joint_" + z["name"], "x": z["x"] + JOINT_OFFSET}
             for z in own if z["name"] not in ("account_number", "branch")]
    return own + joint


def anchors() -> list[dict]:
    return json.loads(ANCHORS_FILE.read_text(encoding="utf-8")) \
        if ANCHORS_FILE.exists() else []


def build_anchors(upright_scan, margin: int = 30) -> list[dict]:
    """Anchor words from an upright scan of the form: the distinctive printed
    words, minus anything inside or beside a field box (that is handwriting).
    Only words and coordinates are kept, never the scan."""
    from ocr.align import build_anchors as pick
    from ocr.tesseract_engine import ocr_page
    fields = zones()
    words = []
    for w in ocr_page(upright_scan).words:
        cx, cy = w["left"] + w["width"] / 2, w["top"] + w["height"] / 2
        if any(z["x"] - margin <= cx <= z["x"] + z["w"] + margin
               and z["y"] - margin <= cy <= z["y"] + z["h"] + margin for z in fields):
            continue
        if w.get("conf", 100) >= 70:
            words.append({"text": w["text"], "x": cx, "y": cy})
    found = pick(words)
    ANCHORS_FILE.write_text(json.dumps(found, indent=1) + chr(10), encoding="utf-8")
    return found


def document_type() -> dict:
    doc_type = {"name": "Home loan application", "regex": TITLE_PATTERN,
                "mode": "zone", "zones": zones()}
    if anchors():
        doc_type["anchors"] = anchors()
    return doc_type
