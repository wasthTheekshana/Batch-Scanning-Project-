"""The Commercial Bank education loan application (docs/education-loan-english.pdf):
where each applicant field sits, and a helper that types values into it.

Zones are in PDF points (A4: 595 x 842) and scale to any scan resolution.
Only the Applicant (left) column is read; the Joint Applicant column mirrors it
at +279 pt and can be added the same way.
"""
from __future__ import annotations

from pathlib import Path

REF = {"ref_w": 595, "ref_h": 842}
TEXT_CLEAN = r"^[\s|.,;:_-]*([^|]+?)[\s|.,;:_-]*$"   # drop stray box-edge marks

# name, x, y, w, h (points), extra zone keys, sample value
FIELDS = [
    ("account_number", 98, 205, 154, 17,
     {"comb": True, "charset": "digits", "regex": r"(\d{6,})"}, "0012345678"),
    ("branch", 98, 226, 154, 14, {"lines": 1, "regex": TEXT_CLEAN},
     "Colombo Fort"),
    ("applicant_name", 87, 290, 204, 28, {"lines": 2, "regex": TEXT_CLEAN},
     "NIMAL KUMARA PERERA"),
    ("date_of_birth", 87, 320, 135, 16,
     {"comb": True, "charset": "digits", "regex": r"(\d{8})"}, "12011990"),
    ("mobile", 64, 505, 86, 15, {"lines": 1, "regex": r"(\d[\d ]{7,}\d)"},
     "0771234567"),
    ("email", 64, 534, 226, 14, {"lines": 1, "regex": r"(\S+@\S+)"},
     "nimal.perera@example.com"),
    ("nic_number", 66, 553, 182, 19,
     {"comb": True, "charset": "nic", "regex": r"(\d{9}[VvXx]|\d{12})"},
     "199012345678"),
    ("passport_number", 95, 572, 154, 19,
     {"comb": True, "charset": "alnum"}, "N1234567"),
    ("address", 28, 427, 264, 32, {"lines": 2, "regex": TEXT_CLEAN},
     "25 Temple Road, Colombo 07"),
]

TITLE_PATTERN = r"education\s+loan\s+application"
# headings that only the form's later pages carry (typed, not read)
LATER_PAGES_PATTERN = r"course\s+details|supplementary\s+limit|relative\s+mentioned\s+below"
SAMPLE = {name: value for name, *_rest, value in FIELDS}


def zones() -> list[dict]:
    return [{"name": name, "x": x, "y": y, "w": w, "h": h, **REF, **extra}
            for name, x, y, w, h, extra, _ in FIELDS]


def document_type() -> dict:
    """The document type for the Bank template (regions, not text patterns)."""
    return {"name": "Education loan application", "regex": TITLE_PATTERN,
            "continuation_regex": LATER_PAGES_PATTERN,
            "mode": "zone", "zones": zones()}


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
    values = {**SAMPLE, **(values or {})}
    source = fitz.open(str(src))
    doc = fitz.open()
    doc.insert_pdf(source, from_page=0,
                   to_page=source.page_count - 1 if all_pages else 0)
    page = doc[0]
    for name, x, y, w, h, extra, _ in FIELDS:
        text = str(values.get(name, ""))
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
