"""The Commercial Bank PERSONAL LOAN and CREDIT CARD applications, read by region.

Same idea as scripts/home_loan_form.py: boxes were measured on a 300 dpi render of
a real filled-in sample (the reference frame below) and the reader lines them up
with any scan through the anchors, the printed words of the form.

Personal loan: the Applicant (left) column is read as the plain field names, the
Joint Applicant column as joint_<name> (JOINT_OFFSET px to the right).
Credit card: one applicant; the person named on the form is read as applicant_name.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ---- Personal loan ------------------------------------------------------------------
PERSONAL_REF = {"ref_w": 2463, "ref_h": 3496}
PERSONAL_JOINT_OFFSET = 1015
PERSONAL_TITLE = r"personal\s+loan\s+application"
TEXT_CLEAN = r"^[\s|.,;:_-]*([^|]+?)[\s|.,;:_-]*$"       # drop stray box-edge marks
EMAIL = r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+)"

# name, x, y, w, h (px of the reference scan), extra keys
PERSONAL_BOXES = [
    ("account_number", 515, 950, 555, 60, {"comb": True, "charset": "digits", "regex": r"(\d{6,})"}),
    ("branch", 515, 1025, 550, 50, {"lines": 1, "regex": TEXT_CLEAN, "hand": True, "kind": "branch"}),
    ("applicant_name", 480, 1236, 722, 112, {"lines": 2, "regex": TEXT_CLEAN, "hand": True, "kind": "name"}),
    ("date_of_birth", 480, 1348, 490, 50, {"comb": True, "charset": "digits", "regex": r"(\d{8})"}),
    ("mobile", 395, 1985, 310, 52, {"lines": 1, "regex": r"(\d[\d ]{7,}\d)", "hand": True, "kind": "mobile"}),
    ("email", 395, 2080, 810, 50, {"lines": 1, "regex": EMAIL, "hand": True, "kind": "email"}),
    ("nic_number", 492, 2145, 655, 54,
     {"comb": True, "charset": "nic", "regex": r"(\d{9}[VvXx]|\d{12})"}),
    ("passport_number", 492, 2212, 560, 54, {"comb": True, "charset": "alnum"}),
]

# ---- Credit card -----------------------------------------------------------------------
CREDIT_REF = {"ref_w": 2442, "ref_h": 3500}
CREDIT_TITLE = r"credit\s+card\s+application"
CREDIT_LATER = r"authori[sz]ed\s+officer.s\s+declaration|details\s+of\s+a\s+relative|card\s+cent(?:er|re)\s+use"
PERSONAL_LATER = r"monthly\s+financial\s+details|loan\s+details|repayment\s+period"
CREDIT_BOXES = [
    ("branch", 296, 610, 500, 56, {"lines": 1, "regex": TEXT_CLEAN, "hand": True, "kind": "branch"}),
    ("nic_number", 390, 935, 760, 48,
     {"comb": True, "charset": "nic", "regex": r"(\d{9}[VvXx]|\d{12})"}),
    ("date_of_birth", 1290, 935, 560, 48, {"comb": True, "charset": "digits", "regex": r"(\d{8})"}),
    ("mobile", 1420, 990, 660, 50, {"comb": True, "charset": "digits", "regex": r"(\d{9,})"}),
    ("email", 470, 1188, 1735, 50, {"comb": True, "charset": "email", "regex": EMAIL, "hand": True, "kind": "email"}),
    ("applicant_name", 533, 1432, 1620, 100, {"lines": 2, "regex": TEXT_CLEAN, "hand": True, "kind": "name"}),
    ("name_on_card", 1133, 880, 1090, 52, {"comb": True, "charset": "alnum"}),
    ("account_number", 630, 2012, 490, 52, {"lines": 1, "regex": r"(\d[\d ]{5,}\d)", "hand": True, "kind": "mobile"}),   # the settlement account: one long box
]


def _zones(boxes, ref):
    return [{"name": n, "x": x, "y": y, "w": w, "h": h, **ref, **extra}
            for n, x, y, w, h, extra in boxes]


def personal_zones() -> list[dict]:
    own = _zones(PERSONAL_BOXES, PERSONAL_REF)
    joint = [{**z, "name": "joint_" + z["name"], "x": z["x"] + PERSONAL_JOINT_OFFSET}
             for z in own if z["name"] not in ("account_number", "branch")]
    return own + joint


def credit_zones() -> list[dict]:
    return _zones(CREDIT_BOXES, CREDIT_REF)


def _anchors(file: str) -> list[dict]:
    p = HERE / file
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def build_anchors(upright_scan, zones: list[dict], file: str, margin: int = 30) -> list[dict]:
    """Anchor words from an upright scan: the distinctive printed words, minus anything
    inside or beside a field box (that is handwriting). Only words and places are kept."""
    from ocr.align import build_anchors as pick
    from ocr.tesseract_engine import ocr_page
    words = []
    for w in ocr_page(upright_scan).words:
        cx, cy = w["left"] + w["width"] / 2, w["top"] + w["height"] / 2
        if any(z["x"] - margin <= cx <= z["x"] + z["w"] + margin
               and z["y"] - margin <= cy <= z["y"] + z["h"] + margin for z in zones):
            continue
        if w.get("conf", 100) >= 70:
            words.append({"text": w["text"], "x": cx, "y": cy})
    found = pick(words)
    (HERE / file).write_text(json.dumps(found, indent=1) + chr(10), encoding="utf-8")
    return found


def personal_document_type() -> dict:
    t = {"name": "Personal loan application", "regex": PERSONAL_TITLE,
         "continuation_regex": PERSONAL_LATER, "mode": "zone", "zones": personal_zones()}
    if _anchors("personal_loan_anchors.json"):
        t["anchors"] = _anchors("personal_loan_anchors.json")
    return t


def credit_document_type() -> dict:
    t = {"name": "Credit card application", "regex": CREDIT_TITLE,
         "continuation_regex": CREDIT_LATER, "mode": "zone", "zones": credit_zones()}
    if _anchors("credit_card_anchors.json"):
        t["anchors"] = _anchors("credit_card_anchors.json")
    return t
