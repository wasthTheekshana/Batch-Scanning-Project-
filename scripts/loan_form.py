"""The Commercial Bank education loan application (docs/education-loan-english.pdf):
where each applicant field sits, and a helper that types values into it.

Zones are in PDF points (A4: 595 x 842) and scale to any scan resolution.
The Applicant (left) column is read as the plain field names; the Joint
Applicant column mirrors it 280 pt to the right and is read as joint_<name>.
"""
from __future__ import annotations

from pathlib import Path

REF = {"ref_w": 595, "ref_h": 842}
TEXT_CLEAN = r"^[\s|.,;:_-]*([^|]+?)[\s|.,;:_-]*$"   # drop stray box-edge marks

# name, x, y, w, h (points), extra zone keys, sample value
FIELDS = [
    ("account_number", 98, 205, 154, 17,
     {"comb": True, "charset": "digits", "regex": r"(\d{6,})"}, "0012345678"),
    ("branch", 98, 226, 154, 14, {"lines": 1, "regex": TEXT_CLEAN, "hand": True, "kind": "branch"},
     "Colombo Fort"),
    ("applicant_name", 87, 290, 204, 28, {"lines": 2, "regex": TEXT_CLEAN, "hand": True, "kind": "name"},
     "NIMAL KUMARA PERERA"),
    ("date_of_birth", 87, 320, 135, 16,
     {"comb": True, "charset": "digits", "regex": r"(\d{8})"}, "12011990"),
    ("mobile", 64, 505, 86, 15, {"lines": 1, "regex": r"(\d[\d ]{7,}\d)", "hand": True, "kind": "mobile"},
     "0771234567"),
    ("email", 64, 534, 226, 14, {"lines": 1, "regex": r"(\S+@\S+)", "hand": True, "kind": "email"},
     "nimal.perera@example.com"),
    ("nic_number", 66, 553, 182, 19,
     {"comb": True, "charset": "nic", "regex": r"(\d{9}[VvXx]|\d{12})"},
     "199012345678"),
    ("passport_number", 95, 572, 154, 19,
     {"comb": True, "charset": "alnum"}, "N1234567"),
    ("address", 28, 427, 264, 32, {"lines": 2, "regex": TEXT_CLEAN},
     "25 Temple Road, Colombo 07"),
]

JOINT_OFFSET = 280
JOINT_SAMPLE = {"name": "KAMALA DEVI PERERA", "date_of_birth": "03051992",
                "mobile": "0712345678", "email": "kamala.perera@example.com",
                "nic": "199212345678", "passport": "N7654321",
                "address": "25 Temple Road, Colombo 07"}
# the joint applicant has their own personal details (not the account or branch)
JOINT_FIELDS = [
    (f"joint_{name}", x + JOINT_OFFSET, y, w, h, extra,
     JOINT_SAMPLE[{"applicant_name": "name", "nic_number": "nic",
                   "passport_number": "passport"}.get(name, name)])
    for name, x, y, w, h, extra, _ in FIELDS
    if name not in ("account_number", "branch")]

TITLE_PATTERN = r"education\s+loan\s+application"
# headings that only the form's later pages carry (typed, not read)
LATER_PAGES_PATTERN = r"course\s+details|supplementary\s+limit|relative\s+mentioned\s+below"
SAMPLE = {name: value for name, *_rest, value in FIELDS}


ANCHORS_FILE = Path(__file__).with_name("loan_form_anchors.json")


def build_anchors(src: str | Path) -> list[dict]:
    """Distinctive printed words of the blank form with their positions (page 1,
    in points): what lets the reader line the zones up with any scan of it.
    Written to loan_form_anchors.json; only words and coordinates, never the
    form itself."""
    import json

    import fitz

    from ocr.align import build_anchors as pick
    with fitz.open(str(src)) as doc:
        words = [{"text": w[4], "x": (w[0] + w[2]) / 2, "y": (w[1] + w[3]) / 2}
                 for w in doc[0].get_text("words")]
    anchors = pick(words)
    ANCHORS_FILE.write_text(json.dumps(anchors, indent=1) + chr(10),
                            encoding="utf-8")
    return anchors


def anchors() -> list[dict]:
    import json
    return json.loads(ANCHORS_FILE.read_text(encoding="utf-8"))         if ANCHORS_FILE.exists() else []


def zones() -> list[dict]:
    return [{"name": name, "x": x, "y": y, "w": w, "h": h, **REF, **extra}
            for name, x, y, w, h, extra, _ in FIELDS + JOINT_FIELDS]


def document_type() -> dict:
    """The document type for the Bank template (regions, not text patterns)."""
    doc_type = {"name": "Education loan application", "regex": TITLE_PATTERN,
                "continuation_regex": LATER_PAGES_PATTERN,
                "mode": "zone", "zones": zones()}
    if anchors():
        doc_type["anchors"] = anchors()
    return doc_type


def _comb_cells(page, x, y, w, h):
    xs = set()
    for drawing in page.get_drawings():
        r = drawing["rect"]
        if r.x0 >= x - 3 and r.x1 <= x + w + 13 and r.y0 >= y - 3 and r.y1 <= y + h + 3:
            for item in drawing["items"]:
                if item[0] == "l" and abs(item[1].x - item[2].x) < 0.5:
                    xs.add(round(item[1].x, 1))
                if item[0] == "re":
                    xs.add(round(item[1].x0, 1))
                    xs.add(round(item[1].x1, 1))
    xs = sorted(xs)
    return [(a, b) for a, b in zip(xs, xs[1:]) if 12 <= b - a <= 17]


def fill_first_page(src: str | Path, out: str | Path,
                    values: dict | None = None, all_pages: bool = False) -> Path:
    """A copy of the form with `values` typed into the applicant's boxes on
    the first page (the other pages stay blank, and are kept only with
    all_pages=True). Used to test and demonstrate the zones."""
    import fitz
    values = {**SAMPLE, **(values or {})}       # joint_* only when given
    source = fitz.open(str(src))
    doc = fitz.open()
    doc.insert_pdf(source, from_page=0,
                   to_page=source.page_count - 1 if all_pages else 0)
    page = doc[0]
    for name, x, y, w, h, extra, _ in FIELDS + JOINT_FIELDS:
        if name not in values:
            continue
        text = str(values[name])
        if extra.get("comb"):
            for char, (x0, x1) in zip(text, _comb_cells(page, x, y, w, h)):
                page.insert_text((x0 + (x1 - x0) / 2 - 3, y + h / 2 + 3.5), char,
                                 fontsize=10, fontname="helv")
        elif extra.get("lines") == 2:       # rows are only 10 pt tall: write small, inside
            page.insert_text((x + 7, y + 10), text, fontsize=8, fontname="helv")
        else:
            page.insert_text((x + 7, y + min(h, 14) - 3), text, fontsize=10,
                             fontname="helv")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    doc.close()
    source.close()
    return out
