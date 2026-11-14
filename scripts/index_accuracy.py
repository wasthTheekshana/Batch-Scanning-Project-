"""How accurately are the important index fields read from real filled-in forms?

    python scripts/index_accuracy.py SAMPLE_DIR [--overlay OUT_DIR]

SAMPLE_DIR holds 300 dpi page images named personal_1.png, credit_1.png, home_1.png
(render the sample PDFs at 300 dpi). For each page this runs the application's own
index path (OCR the page, pick the document type from its text, read that type's zones)
and compares every field with the TRUTH below. --overlay writes each page with its
zones drawn, to check that the boxes sit on the form.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cv2

# what is written on the samples (read by eye). The six fields the bank cares about most
# come first; the rest are measured too.
IMPORTANT = ["account_number", "applicant_name", "nic_number", "email", "mobile", "branch"]
TRUTH = {
    "personal_1": {"account_number": "3258341563", "applicant_name": "D V W Thumar Kalindu Perera",
                   "nic_number": "553216789V", "email": "abc@gmail.com", "mobile": "0769400460",
                   "branch": "Jaffna", "date_of_birth": "23031990"},
    "credit_1": {"applicant_name": "Tharindu Madushanka Perera", "nic_number": "534210056718",
                 "email": "qbPLLeqbcii@gmail.com", "mobile": "0760070011", "branch": "Matara",
                 "date_of_birth": "30051997"},
}


def norm(field: str, value: str) -> str:
    v = (value or "").strip()
    if field in ("applicant_name", "branch"):
        return re.sub(r"[^a-z]", "", v.lower())
    if field in ("email",):
        return v.lower().replace(" ", "")
    if field == "nic_number":
        return v.upper().replace(" ", "")
    return re.sub(r"\s", "", v)


def template_cfg() -> dict:
    import json
    path = Path(__file__).resolve().parents[1] / "projects" / "templates" / "bank_customer_file.json"
    return json.loads(path.read_text(encoding="utf-8"))["config"]["ocr"]


def read_page(img, cfg) -> tuple[str | None, dict]:
    from ocr.doc_types import page_zone_groups
    from ocr.orientation import upright
    from ocr.tesseract_engine import ocr_page
    from ocr.zone_extractor import extract_fields
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _turn, img, result = upright(img, ocr_page(gray), ocr_page)
    groups, doc_type = page_zone_groups(cfg, result.text)
    fields = {}
    for zones, mode in groups:
        if mode == "zone":
            fields.update(extract_fields(img, zones, words=getattr(result, "words", None)))
    return doc_type, fields


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("samples")
    ap.add_argument("--overlay")
    args = ap.parse_args(argv)
    cfg = template_cfg()
    total = right = 0
    for name, truth in TRUTH.items():
        path = Path(args.samples) / f"{name}.png"
        img = cv2.imread(str(path))
        doc_type, fields = read_page(img, cfg)
        print(f"\n{name}: document type = {doc_type}")
        for f in IMPORTANT + [k for k in truth if k not in IMPORTANT]:
            if f not in truth:
                continue
            got = fields[f].value if f in fields else ""
            ok = norm(f, got) == norm(f, truth[f])
            total += f in IMPORTANT
            right += ok and f in IMPORTANT
            conf = fields[f].confidence if f in fields else 0
            print(f"  {'OK  ' if ok else 'MISS'} {f:15} read {got!r:34} want {truth[f]!r}  (conf {conf:.0f})")
    print(f"\nIMPORTANT FIELDS RIGHT: {right}/{total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
