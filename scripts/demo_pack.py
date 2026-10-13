"""Build every file the bank demo needs into <repo>/demo_kit/.

    python scripts/demo_pack.py

  cover_sheets_CIF-100001-100020.pdf   20 full-page barcode cover sheets
  barcode_labels_CIF-100001-100020.pdf sticker labels (3 copies each)
  bank_batch.pdf                       two customers in one stack (cover sheet
                                       + sticker styles, the real loan form)
  REHEARSAL_print_me.pdf               one customer file to print and scan
  invoice_ACME-*.pdf, ...              the invoice samples
All data in them is made up.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))


def main() -> int:
    import demo_kit as dk
    import rehearsal
    from scanning.cover_sheets import sequence_ids, write_cover_sheets

    dk.KIT.mkdir(exist_ok=True)
    ids = sequence_ids("CIF-", 100001, 20)
    write_cover_sheets(ids, dk.KIT / "cover_sheets_CIF-100001-100020.pdf")
    write_cover_sheets(ids, dk.KIT / "barcode_labels_CIF-100001-100020.pdf",
                       layout="labels", copies=3)
    dk.build()                  # invoices + the invoice batch
    dk.build_bank()             # the two-customer bank batch
    rehearsal.build()           # the print-and-scan rehearsal
    print("\nDemo files are in", dk.KIT)
    for name in ("cover_sheets_CIF-100001-100020.pdf",
                 "barcode_labels_CIF-100001-100020.pdf", "bank_batch.pdf",
                 "REHEARSAL_print_me.pdf", "invoice_ACME-1.pdf",
                 "demo_batch.pdf"):
        print("  ", name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
