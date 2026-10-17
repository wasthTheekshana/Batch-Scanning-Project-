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
FORM = REPO / "docs" / "education-loan-english.pdf"      # the bank's real form

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




# ---------------------------------------------------------------------------
# Bank scenario: one PDF per customer, split by a barcode cover sheet
# ---------------------------------------------------------------------------
CUSTOMERS = [
    {"cif": "CIF-100234", "name": "Nimal Perera", "nic": "199012345678",
     "app": "APP-55001", "style": "cover",
     "pages": ["nic_front", "nic_back", "app_1", "app_2", "letter"]},
    {"cif": "CIF-100235", "name": "Kamala Silva", "nic": "856789012V",
     "app": "APP-55002", "style": "sticker",
     "pages": ["nic_front", "loan_form" if FORM.exists() else "app_1"]},
]


def _bank_page(c, kind: str, cust: dict, w: float, h: float,
               sticker: bool = False) -> None:
    if sticker:        # a barcode label stuck on the right of the first page
        from reportlab.graphics.barcode import code128
        code128.Code128(cust["cif"], barWidth=1.1, barHeight=40,
                        quiet=True).drawOn(c, w - 190, h - 150)
    c.setFillGray(0.1)
    c.rect(0, h - 36, w, 36, stroke=0, fill=1)     # ink so it is not "blank"
    c.setFillGray(0)
    c.setFont("Helvetica-Bold", 16)
    lines = {
        "nic_front": ["NATIONAL IDENTITY CARD",
                      f"Name: {cust['name'].upper()}",
                      f"ID No: {cust['nic']}", "Date of Birth: 12 Jan 1990"],
        "nic_back": ["NATIONAL IDENTITY CARD - REVERSE",
                     "Address: 25 Temple Road, Colombo 07",
                     "Date of Issue: 03 Feb 2015"],
        "app_1": ["PERSONAL LOAN APPLICATION FORM",
                  f"Application No: {cust['app']}",
                  f"Applicant Name: {cust['name']} Product: Personal Loan",
                  "Requested Amount: LKR 500,000"],
        "app_2": ["APPLICATION FORM - DECLARATION",
                  "I declare the information above is correct.",
                  "Signature: ______________________"],
        "letter": ["CONFIRMATION OF EMPLOYMENT",
                   "To whom it may concern, the above person is a permanent "
                   "employee.", "Human Resources, Example Holdings"],
    }[kind]
    y = h - 90
    for i, text in enumerate(lines):
        c.setFont("Helvetica-Bold" if i == 0 else "Helvetica", 15 if i == 0 else 12)
        c.drawString(50, y, text)
        y -= 28


def build_bank() -> dict[str, Path]:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    KIT.mkdir(exist_ok=True)
    parts: list[Path] = []
    out: dict[str, Path] = {}
    for cust in CUSTOMERS:
        if cust["style"] == "cover":
            cover = KIT / f"bank_cover_{cust['cif']}.pdf"
            _cover_pdf(cover, cust["cif"])
            parts.append(cover)
        for n, kind in enumerate(cust["pages"], 1):
            path = KIT / f"bank_{cust['cif']}_{n}_{kind}.pdf"
            if kind == "loan_form":       # the real 4-page form, typed in
                sys.path.insert(0, str(REPO / "scripts"))
                import loan_form
                loan_form.fill_first_page(
                    FORM, path, all_pages=True,
                    values={"applicant_name": cust["name"].upper(),
                            "nic_number": cust["nic"], "date_of_birth": "05031985",
                            "email": "kamala.silva@example.com",
                            "mobile": "0719876543", "account_number": "0098765432"})
                parts.append(path)
                continue
            c = canvas.Canvas(str(path), pagesize=A4)
            _bank_page(c, kind, cust, *A4,
                       sticker=(cust["style"] == "sticker" and n == 1))
            c.showPage()
            c.save()
            parts.append(path)
    out["batch"] = KIT / "bank_batch.pdf"
    _merge(out["batch"], parts)
    print(f"wrote {out['batch']} ({len(parts)} pages)")
    return out


def run_bank() -> int:
    files = build_bank()
    sys.path.insert(0, str(REPO / ".claude" / "skills" / "run-dokscanner"
                           / "driver"))
    import _harness as h
    sandbox = KIT / "sandbox_bank"
    h.setup_env(sandbox)
    report = h.Report()

    def scenario(app, win):
        import json
        import uuid

        import fitz

        from core.profile_manager import ProfileManager
        from core.template_manager import TemplateManager
        from scanning.file_import import import_files

        root = Path(os.environ["LOCALAPPDATA"]) / "DOKScanner"
        pm = ProfileManager(root / "projects", win.db, data_root=root)
        manager = TemplateManager(win.db)
        manager.seed_built_ins()
        template = next(t for t in manager.list_templates()
                        if t["name"] == "Bank - Customer file")
        cfg = pm.create("Bank - Customer file",
                        config=manager.build_project_config(
                            template["template_id"]))
        pid = cfg["project_id"]

        batch_id = str(uuid.uuid4())
        jobs = import_files([files["batch"]], batch_id, pid, sandbox / "staging")
        expected_pages = 11 if FORM.exists() else 8      # the real form has 4 pages
        report.check("batch pages", len(jobs) == expected_pages, f"({len(jobs)})")
        win.start_batch(batch_id, jobs)
        h.wait_for(app, lambda: len(win.db.list_documents()) >= 2, 120)
        h.pump(app, 8)
        docs = {json.loads(d["extracted_fields"] or "{}").get("barcode"): d
                for d in win.db.list_documents()}
        report.check("one document per customer",
                     set(docs) == {"CIF-100234", "CIF-100235"}, str(sorted(docs)))
        report.check("nothing held for QC (the cover sheet is not scored)",
                     all(d["validation_status"] == "auto_approved"
                         for d in docs.values()),
                     str({k: d["validation_status"] for k, d in docs.items()}))
        out_dir = root / "output"
        pdfs = {p.name.split("_")[0]: p for p in out_dir.rglob("*.pdf")}
        report.check("one PDF per customer, named by its barcode",
                     {"CIF-100234", "CIF-100235"} <= set(pdfs), str(sorted(pdfs)))
        expected = {"CIF-100234": (5, [[1, "NIC", 1], [1, "Application", 3],
                                       [1, "Letter", 5]]),
                    "CIF-100235": ((5, [[1, "NIC", 1],
                                       [1, "Education loan application", 2]])
                                   if FORM.exists() else
                                   (2, [[1, "NIC", 1], [1, "Application", 2]]))}
        for cif, (pages, toc) in expected.items():
            if cif in pdfs:
                with fitz.open(str(pdfs[cif])) as pdf:
                    report.check(f"{cif}.pdf: that customer's pages, no cover",
                                 pdf.page_count == pages,
                                 f"({pdf.page_count} pages, expected {pages})")
                    report.check(f"{cif}.pdf: bookmarks per document type",
                                 pdf.get_toc() == toc, str(pdf.get_toc()))
        index_rows = {}
        import csv as _csv
        for cif in ("CIF-100234", "CIF-100235"):
            matches = sorted(out_dir.glob(f"{cif}_*.csv"))
            if not matches:
                continue
            sidecar = matches[0]
            with open(sidecar, newline="", encoding="utf-8-sig") as f:
                index_rows[cif] = next(_csv.DictReader(f))
        report.check("an index file sits next to each PDF",
                     set(index_rows) == {"CIF-100234", "CIF-100235"}, str(sorted(index_rows)))
        running = out_dir / "index.csv"
        report.check("the running index lists both customers",
                     running.exists() and len(list(_csv.DictReader(
                         open(running, newline="", encoding="utf-8-sig")))) == 2)
        if "CIF-100234" in index_rows:
            report.check("the index carries the customer's fields",
                         index_rows["CIF-100234"]["nic_number"] == "199012345678"
                         and index_rows["CIF-100234"]["barcode"] == "CIF-100234"
                         and index_rows["CIF-100234"]["page_count"] == "5")
        fields = {cif: json.loads(win.db.get_document(d["document_id"])
                                  ["extracted_fields"]) for cif, d in docs.items()}
        for cif, f in fields.items():
            print(f"    {cif}: {f}", flush=True)
        a, b = fields["CIF-100234"], fields["CIF-100235"]
        report.check("first customer: fields from NIC, application and letter",
                     a.get("nic_number") == "199012345678"
                     and a.get("application_number") == "APP-55001"
                     and a.get("applicant_name") == "Nimal Perera"
                     and a.get("doc_types") == "NIC, Application, Letter", str(a))
        if FORM.exists():
            report.check("second customer (barcode sticker + the real bank form)",
                         b.get("nic_number") == "856789012V"
                         and b.get("applicant_name") == "KAMALA SILVA"
                         and b.get("account_number") == "0098765432"
                         and b.get("date_of_birth") == "05031985"
                         and b.get("email") == "kamala.silva@example.com"
                         and b.get("doc_types") == "NIC, Education loan application",
                         str(b))
        else:
            report.check("second customer (barcode sticker): own fields",
                         b.get("nic_number") == "856789012V"
                         and b.get("application_number") == "APP-55002"
                         and b.get("doc_types") == "NIC, Application", str(b))
        return report.summary()

    code = h.run_main(scenario)
    print(f"main() returned {code}")
    return code


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "bank":
        sys.exit(run_bank())
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        build()
        sys.exit(0)
    sys.exit(run())
