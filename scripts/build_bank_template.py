"""Regenerate core/templates/bank_customer_file.json (the built-in
"Bank - Customer file" template): customer-file separation plus the document
types NIC, Education and Home loan applications (regions on the real forms),
generic Application and Letter.

    python scripts/build_bank_template.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bank_forms  # noqa: E402
import home_loan_form  # noqa: E402
import loan_form  # noqa: E402

DATE = r"(\d{1,2}[ ./-](?:[A-Za-z]{3,9}|\d{1,2})[ ./-]\d{2,4})"
NAME = r"([A-Za-z.]+(?: [A-Za-z.]+){0,4}?)"

DOCUMENT_TYPES = [
    # a specific form first: types are tried in order and the first match wins
    bank_forms.credit_document_type(),   # the credit card form also mentions a "supplementary limit"
    # The loan forms share the headings "Loan Details" and "Monthly Financial Details" on their second page, so
    # the forms with words of their own are tried first and the personal loan, whose later-page pattern is the
    # generic one, last. (Otherwise a home or education loan's second page is taken for a personal loan's.)
    home_loan_form.document_type(),      # before the generic Application: its title matches that too
    loan_form.document_type(),
    bank_forms.personal_document_type(),
    {"name": "Application",
     "regex": r"application\s+form|loan\s+application|account\s+opening|application\s+no",
     "zones": [
         {"name": "application_number",
          "regex": r"(?i)Application\s*(?:No|Number|#)\.?\s*[:.]?\s*([A-Z0-9][A-Z0-9\-/]{3,})"},
         {"name": "applicant_name",
          "regex": r"(?i)(?:Applicant|Customer|Full)\s*Name\s*[:.]?\s*" + NAME
                   + r"(?=\s+(?:Product|NIC|ID|Date|Address|Mobile|Tel|Branch|Requested|Amount)\b|$)"},
         {"name": "product",
          "regex": r"(?i)Product\s*[:.]?\s*([A-Za-z]+(?: [A-Za-z]+){0,3}?)"
                   r"(?=\s+(?:Requested|Amount|Branch|Date|Tenure|Rate)\b|$)"},
         {"name": "application_date",
          "regex": r"(?i)Application\s*Date\s*[:.]?\s*" + DATE}]},
    {"name": "NIC",
     # the card's title (OCR often drops the start of "IDENTITY", leaving
     # "NTITY CARD") or a line only a card has. Numbers alone are not used: a
     # letter with a long reference number would be taken for a card.
     "regex": r"national\s+identity\s+card|identity\s+card|ntity\s+card"
              r"|holder.?s\s+signature",
     "zones": [
         {"name": "nic_number", "regex": r"\b(\d{9}[VvXx]|\d{12})\b"},
         {"name": "nic_name",
          "regex": r"(?i)Name\s*[:.]?\s*" + NAME
                   + r"(?=\s+(?:ID|NIC|Date|Address|Sex|Gender|Given)\b|$)"},
         {"name": "nic_date_of_birth",
          "regex": r"(?i)(?:Date\s*of\s*Birth|DOB)\s*[:.]?\s*" + DATE}]},
    {"name": "Letter",
     "regex": r"to\s+whom\s+it\s+may\s+concern|dear\s+(?:sir|madam)|confirmation\s+of|subject\s*:",
     "zones": [
         {"name": "letter_subject",
          "regex": r"(?i)Subject\s*:\s*([^:]{3,80}?)(?=\s+(?:Dear|To whom|Ref)\b|$)"},
         {"name": "letter_reference",
          "regex": r"(?i)(?:Our\s*)?Ref(?:erence)?\s*(?:No)?\.?\s*[:.]\s*([A-Z0-9][A-Z0-9\-/]{2,})"},
         {"name": "letter_date", "regex": r"(?i)(?<!of )Date\s*[:.]?\s*" + DATE}]},
]

TEMPLATE = {
    "name": "Bank - Customer file",
    "category": "Banking",
    "config": {
        "separation": {"strategy": ["barcode"], "barcode_role": "auto",
                       "cover_max_chars": 40, "blank_threshold": 0.02,
                       "drop_blank_pages": True},
        "ocr": {"lang": "eng", "mode": "full_page", "confidence_threshold": 0.75,
                "zones": [], "document_types": DOCUMENT_TYPES},
        "export": {"filename_template": "{barcode}_{counter:04d}",
                   "index": {"enabled": True, "format": "csv", "running_csv": True}},
        "qc": {"enabled": True,
               "stages": ["punch_hole", "rotation", "margin", "color", "advanced"],
               "pass_score": 85, "blur_warn_threshold": 80,
               "blur_fail_threshold": 30, "dpi_warn": 200, "dpi_fail": 150},
        "enhancement_profile": {"deskew": True, "crop": True, "clahe": True,
                                "despeckle": True, "whiten_bg": True,
                                "target_dpi": 300},
        "features": {"auto_index": True, "duplicate_check": True},
    },
}

if __name__ == "__main__":
    out = HERE.parent / "core" / "templates" / "bank_customer_file.json"
    out.write_text(json.dumps(TEMPLATE, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
