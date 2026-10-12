import json
from pathlib import Path

from ocr.zone_extractor import extract_fields_from_text

ZONES = json.loads((Path(__file__).parent.parent / "core" / "templates"
                    / "invoices.json").read_text(encoding="utf-8")
                   )["config"]["ocr"]["zones"]

TEXT = ("ACME TRADING (PVT) LTD 12 Main Street TAX INVOICE "
        "Invoice No: INV-20391 Invoice Date: 18 Mar 2026 "
        "Your Ref # PO77812 Sub Total 1,000.00 Total LKR 1,180.00")


def test_label_anchored_zones_pick_the_real_values():
    got = {k: v.value for k, v in extract_fields_from_text(TEXT, 0.9, ZONES).items()}
    assert got == {
        "invoice_number": "INV-20391",
        "vendor_name": "ACME TRADING (PVT) LTD",
        "invoice_date": "18 Mar 2026",
        "amount": "1,180.00",
        "po_reference": "PO77812",
    }
