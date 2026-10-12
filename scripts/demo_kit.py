"""Demo kit: sample barcode PDFs + a headless end-to-end run of the real app.

    python scripts/demo_kit.py build          # only write the PDFs
    python scripts/demo_kit.py run            # PDFs + run the real app headless

PDFs (written to <repo>/demo_kit/, all data is made up):
  invoice_ACME-1.pdf (2 pages), invoice_ACME-2.pdf, invoice_ACME-3.pdf
  cover_CASE-0001.pdf / cover_CASE-0002.pdf   Code128 cover sheets: a page with a
        barcode starts a new document and the barcode value is saved on it
  blank_separator.pdf                         a blank page: splits and is dropped
  demo_batch.pdf                              all of the above in scan order

`run` additionally (in a throw-away app data dir, never your real one) creates a
custody bundle, prints its QR label sheet, puts it in front of the batch as
demo_batch_with_custody.pdf, scans the batch through the real MainWindow +
pipeline, and screenshots the result to demo_kit/screens/.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
KIT = REPO / "demo_kit"

INVOICES = [
    ("ACME-1", "ACME TRADING (PVT) LTD", "INV-20391", "18 Mar 2026",
     "PO77812", "1,180.00", 2),
    ("ACME-2", "ACME TRADING (PVT) LTD", "INV-20392", "19 Mar 2026",
     "PO77813", "52,300.50", 1),
    ("ACME-3", "ACME TRADING (PVT) LTD", "INV-20393", "20 Mar 2026",
     "PO77814", "9,875.00", 1),
]


def _invoice_pdf(path: Path, vendor, number, date, ref, total, pages) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    for page in range(1, pages + 1):
        # a dark band: a sparse page has under 2% ink and the blank-page
        # separator would drop it as a blank sheet (real invoices have more)
        c.setFillGray(0.1)
        c.rect(0, h - 36, w, 36, stroke=0, fill=1)
        c.setFillGray(0)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(50, h - 70, vendor)
        c.setFont("Helvetica", 11)
        c.drawString(50, h - 90, "12 Main Street, Colombo 03, Sri Lanka")
        c.setFont("Helvetica-Bold", 14)
        c.drawString(50, h - 120, "TAX INVOICE")
        c.setFont("Helvetica", 12)
        if page == 1:
            c.drawString(50, h - 150, f"Invoice No: {number}")
            c.drawString(50, h - 170, f"Invoice Date: {date}")
            c.drawString(50, h - 190, f"Your Ref # {ref}")
            c.drawString(50, h - 230, "Description                         Amount")
            c.drawString(50, h - 250, "Freight charges                     "
                         f"{total}")
            c.drawString(50, h - 300, f"Total LKR {total}")
        else:
            c.drawString(50, h - 150, f"Invoice {number} - continuation sheet")
            c.drawString(50, h - 170, "Terms and conditions apply.")
        c.drawString(50, 40, f"Page {page} of {pages}")
        c.showPage()
    c.save()


def _cover_pdf(path: Path, code: str) -> None:
    from reportlab.graphics.barcode import code128
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(w / 2, h - 90, "DOCUMENT COVER SHEET")
    code128.Code128(code, barWidth=1.6, barHeight=70, quiet=True).drawOn(
        c, (w - 280) / 2, h - 220)
    c.setFont("Helvetica", 14)
    c.drawCentredString(w / 2, h - 245, code)
    c.save()


def _blank_pdf(path: Path) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=A4)
    c.showPage()
    c.save()


def _merge(out: Path, parts: list[Path]) -> None:
    import fitz
    doc = fitz.open()
    for p in parts:
        with fitz.open(str(p)) as src:
            doc.insert_pdf(src)
    doc.save(str(out))
    doc.close()


def build() -> dict[str, Path]:
    KIT.mkdir(exist_ok=True)
    files: dict[str, Path] = {}
    for tag, *rest in INVOICES:
        files[tag] = KIT / f"invoice_{tag}.pdf"
        _invoice_pdf(files[tag], *rest)
    for code in ("CASE-0001", "CASE-0002"):
        files[code] = KIT / f"cover_{code}.pdf"
        _cover_pdf(files[code], code)
    files["blank"] = KIT / "blank_separator.pdf"
    _blank_pdf(files["blank"])
    # scan order: case 1 = cover + 2-page invoice; case 2 = cover + invoice;
    # blank separator; then an invoice with no cover sheet
    order = ["CASE-0001", "ACME-1", "CASE-0002", "ACME-2", "blank", "ACME-3"]
    files["batch"] = KIT / "demo_batch.pdf"
    _merge(files["batch"], [files[k] for k in order])
    print(f"wrote {len(files)} PDFs to {KIT}")
    return files


ZONES = [
    {"name": "invoice_number", "regex":
        r"(?i)invoice\s*(?:no\.?|number|#)\s*[:.]?\s*([A-Z0-9][A-Z0-9\-/]{3,})"},
    {"name": "vendor_name", "regex":
        r"^\s*([A-Z][A-Za-z&.,' ]{2,60}?(?:\(PVT\)\s*)?(?:LTD|LIMITED|PLC|INC)\.?)"},
    {"name": "invoice_date", "regex":
        r"(?i)(?:invoice\s*)?date\s*[:.]?\s*(\d{1,2}[ /.-](?:[A-Za-z]{3}|\d{1,2})"
        r"[ /.-]\d{2,4})"},
    {"name": "amount", "regex":
        r"(?i)(?<!sub )(?<!sub)(?:grand\s*)?total(?:\s*(?:lkr|amount|due))?"
        r"\s*[:.]?\s*([\d,]+\.\d{2})"},
    {"name": "po_reference", "regex":
        r"(?i)(?:po\b|purchase\s*order|your\s*ref)\w*\s*#?\s*[:.]?\s*"
        r"([A-Z0-9][A-Z0-9\-/]{3,})"},
]


def run() -> int:
    files = build()
    sys.path.insert(0, str(REPO / ".claude" / "skills" / "run-dokscanner"
                           / "driver"))
    import _harness as h
    sandbox = KIT / "sandbox"
    h.setup_env(sandbox)
    shots = KIT / "screens"
    report = h.Report()

    def scenario(app, win):
        import json
        import uuid

        from core.profile_manager import DEFAULT_PROJECT, ProfileManager
        from custody.bundle_manager import create_bundle, generate_label_sheet
        from scanning.file_import import import_files

        root = Path(os.environ["LOCALAPPDATA"]) / "DOKScanner"
        pm = ProfileManager(root / "projects", win.db, data_root=root)
        import copy
        cfg = copy.deepcopy(DEFAULT_PROJECT)
        cfg["separation"]["strategy"] = ["barcode", "blank_page"]
        cfg["ocr"]["mode"] = "full_page"
        cfg["ocr"]["zones"] = ZONES
        cfg = pm.create("Demo - Invoices", config=cfg)
        pid = cfg["project_id"]

        bundle = create_bundle(win.db, pid, "DEMO box 12 - 3 invoices",
                               audit=win.audit)
        label = generate_label_sheet(bundle, KIT / "custody_label_DEMO.pdf")
        batch_pdf = KIT / "demo_batch_with_custody.pdf"
        _merge(batch_pdf, [label, files["batch"]])
        report.note(f"bundle {bundle['bundle_id'][:8]} status="
                    f"{win.db.get_bundle(bundle['bundle_id'])['status']}")

        batch_id = str(uuid.uuid4())
        jobs = import_files([batch_pdf], batch_id, pid, sandbox / "staging")
        report.check("PDF exploded to pages", len(jobs) == 8, f"({len(jobs)} pages)")
        win.start_batch(batch_id, jobs)
        done = h.wait_for(app, lambda: len(win.db.list_documents()) >= 3, 120)
        h.pump(app, 6)   # let the auto-approved document finish export
        docs = sorted(win.db.list_documents(), key=lambda d: d["page_range"])
        report.check("batch produced 3 documents", done and len(docs) == 3,
                     f"({len(docs)} documents)")
        for d in docs:
            f = json.loads(d["extracted_fields"] or "{}")
            print(f"    doc pages {d['page_range']:>4} status={d['validation_status']}"
                  f" barcode={f.get('barcode')!r} fields="
                  f"{ {k: v for k, v in f.items() if k != 'barcode'} }", flush=True)
        by_barcode = {json.loads(d["extracted_fields"] or "{}").get("barcode"): d
                      for d in docs}
        report.check("cover barcodes became the documents' barcode",
                     {"CASE-0001", "CASE-0002", None} == set(by_barcode))
        first = json.loads(by_barcode["CASE-0001"]["extracted_fields"])
        report.check("auto-index read the real invoice values",
                     first.get("invoice_number") == "INV-20391"
                     and first.get("amount") == "1,180.00"
                     and first.get("po_reference") == "PO77812"
                     and first.get("invoice_date") == "18 Mar 2026", str(first))
        report.check("custody label sheet was not made into a document",
                     all("custody" not in (d["storage_path"] or "") for d in docs)
                     and sum(int(d["page_range"].split("-")[1])
                             - int(d["page_range"].split("-")[0]) + 1
                             for d in docs) <= 6)
        b = win.db.get_bundle(bundle["bundle_id"])
        events = [e["stage"] for e in win.db.list_custody_events(bundle["bundle_id"])]
        report.check("scanning checkpoint logged automatically",
                     b["status"] == "scanning" and "scanning" in events,
                     f"status={b['status']} events={events}")

        # operator records the remaining custody steps
        from custody.bundle_manager import record_checkpoint
        for stage in ("qc", "storage"):
            record_checkpoint(win.db, bundle["bundle_id"], stage, "admin",
                              audit=win.audit)

        # screenshots
        for i in range(win.tabs.count()):
            name = win.tabs.tabText(i)
            win.tabs.setCurrentIndex(i)
            if name == "Custody":
                panel = win.custody_panel
                panel.project_id = pid   # the panel follows the first project
                panel.refresh()
                panel._reselect(bundle["bundle_id"])
            if name == "Index":
                win.index_editor.refresh()
                win.index_editor.filter_combo.setCurrentIndex(1)
                if win.index_editor.doc_list.count():
                    win.index_editor.doc_list.setCurrentRow(0)
            h.shot(app, win, shots / f"{i:02d}-{name.lower().replace(' ', '-')}.png")
        print(f"screenshots in {shots}", flush=True)
        return report.summary()

    code = h.run_main(scenario)
    print(f"main() returned {code}")
    return code


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        build()
        sys.exit(0)
    sys.exit(run())
