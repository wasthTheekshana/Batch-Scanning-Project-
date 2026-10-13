"""Full rehearsal with the real Kodak scanner.

    python scripts/rehearsal.py build   # writes demo_kit/REHEARSAL_print_me.pdf
    python scripts/rehearsal.py scan    # scan what is in the feeder, run it through
                                        # the app code, check every result
    python scripts/rehearsal.py simulate  # same checks without a scanner: the PDF
                                        # is rendered like a duplex scan (blank backs)

Print REHEARSAL_print_me.pdf on plain paper, ONE-SIDED, at 100% scale (7 sheets:
a barcode cover sheet, an ID card page, the bank's 4-page loan form, a letter),
stack the sheets in that order, load them in the feeder, then run `scan`.
The scan uses the project's own settings (300 dpi grayscale, duplex), so the
blank backs of the one-sided sheets are scanned too and must be discarded.
Everything runs in a throw-away data folder: your real data is not touched.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
KIT = REPO / "demo_kit"

CUSTOMER = {"cif": "CIF-200001", "name": "SAMANTHA FERNANDO", "nic": "905551234V",
            "form": {"account_number": "0011223344", "branch": "Kandy City Centre",
                     "applicant_name": "SAMANTHA FERNANDO", "date_of_birth": "17081990",
                     "mobile": "0765551234", "email": "samantha.f@example.com",
                     "nic_number": "905551234V", "passport_number": "N5551234",
                     "address": "14 Lake Road, Kandy"}}


def build() -> Path:
    import demo_kit as dk
    import loan_form
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    KIT.mkdir(exist_ok=True)
    cover = KIT / "rehearsal_cover.pdf"
    dk._cover_pdf(cover, CUSTOMER["cif"])
    nic = KIT / "rehearsal_nic.pdf"
    c = canvas.Canvas(str(nic), pagesize=A4)
    dk._bank_page(c, "nic_front", {"name": "Samantha Fernando", "nic": CUSTOMER["nic"],
                                   "app": "", "cif": CUSTOMER["cif"]}, *A4)
    c.showPage()
    c.save()
    parts = [cover, nic]
    if dk.FORM.exists():
        form = KIT / "rehearsal_form.pdf"
        loan_form.fill_first_page(dk.FORM, form, CUSTOMER["form"], all_pages=True)
        parts.append(form)
    letter = KIT / "rehearsal_letter.pdf"
    c = canvas.Canvas(str(letter), pagesize=A4)
    dk._bank_page(c, "letter", {"name": "Samantha Fernando", "nic": "", "app": "",
                                "cif": CUSTOMER["cif"]}, *A4)
    c.showPage()
    c.save()
    parts.append(letter)
    out = KIT / "REHEARSAL_print_me.pdf"
    dk._merge(out, parts)
    import fitz
    with fitz.open(str(out)) as pdf:
        print(f"wrote {out} ({pdf.page_count} pages): print ONE-SIDED, 100% scale")
    return out


def acquire(folder: Path) -> list[Path]:
    """Scan the feeder with the project's default settings."""
    from scanning.wia_source import WIASource
    from scanning.base import ScannerUnavailable
    source = WIASource()
    devices = source.list_devices()
    if not devices:
        raise SystemExit("No scanner found.")
    print(f"scanner: {devices[0]}")
    source.connect(devices[0])
    folder.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    def on_page(data: bytes, number: int) -> None:
        path = folder / f"page_{number:04d}.png"
        path.write_bytes(data)
        paths.append(path)
        print(f"  page {number} ({len(data) // 1024} KB)", flush=True)

    started = time.time()
    try:
        source.acquire(300, "grayscale", True, on_page=on_page)
    except ScannerUnavailable as e:
        raise SystemExit(f"scan stopped: {e}")
    print(f"{len(paths)} images in {time.time() - started:.0f}s")
    return paths


def simulated_scan(folder: Path) -> list[Path]:
    """The print-me PDF as a duplex scanner would deliver it: every sheet's
    front, each followed by its blank back, at 300 dpi grayscale."""
    import cv2
    import fitz
    import numpy as np
    folder.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    with fitz.open(str(KIT / "REHEARSAL_print_me.pdf")) as pdf:
        for number, page in enumerate(pdf, 1):
            pix = page.get_pixmap(dpi=300, colorspace=fitz.csGRAY)
            front = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
            for side, image in (("front", front), ("back", np.full_like(front, 250))):
                path = folder / f"page_{len(paths) + 1:04d}_{side}.png"
                cv2.imwrite(str(path), image)
                paths.append(path)
    return paths


def scan(simulate: bool = False) -> int:
    import shutil
    pages_dir = KIT / "rehearsal_scan"
    shutil.rmtree(pages_dir, ignore_errors=True)
    if simulate:
        build()
        paths = simulated_scan(pages_dir)
    else:
        paths = acquire(pages_dir)
    if not paths:
        raise SystemExit("The feeder was empty.")

    sys.path.insert(0, str(REPO / ".claude" / "skills" / "run-dokscanner" / "driver"))
    import _harness as h
    sandbox = KIT / "sandbox_rehearsal"
    h.setup_env(sandbox)
    report = h.Report()

    def scenario(app, win):
        import uuid

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
                        config=manager.build_project_config(template["template_id"]))
        batch_id = str(uuid.uuid4())
        jobs = import_files(paths, batch_id, cfg["project_id"], sandbox / "staging")
        report.note(f"{len(jobs)} scanned images")
        win.start_batch(batch_id, jobs)
        started = time.time()
        h.wait_for(app, lambda: len(win.db.list_documents()) >= 1, 240)
        h.pump(app, 20)
        docs = win.db.list_documents()
        report.note(f"processed in {time.time() - started:.0f}s")
        report.check("one document (one customer)", len(docs) == 1, f"({len(docs)})")
        if not docs:
            return report.summary()
        doc = docs[0]
        fields = json.loads(doc["extracted_fields"] or "{}")
        detail = json.loads(doc["qc_detail"] or "{}")
        page_types = [info.get("doc_type") for _n, info in sorted(
            detail.get("pages", {}).items(), key=lambda kv: int(kv[0]))]
        print(f"    status={doc['validation_status']} pages={len(page_types)} types={page_types}")
        print(f"    fields={fields}")
        report.check("the barcode cover sheet named the customer",
                     fields.get("barcode") == CUSTOMER["cif"], str(fields.get("barcode")))
        want = 6 if (REPO / "docs" / "education-loan-english.pdf").exists() else 3
        report.check("blank backs and the cover were discarded", len(page_types) == want,
                     f"({len(page_types)} pages, expected {want})")
        report.check("page types found",
                     fields.get("doc_types", "").startswith("NIC"), fields.get("doc_types", ""))
        expected = dict(CUSTOMER["form"])
        wrong = {k: (fields.get(k), v) for k, v in expected.items()
                 if (REPO / "docs" / "education-loan-english.pdf").exists()
                 and fields.get(k) != v}
        report.check("every field of the loan form read exactly", not wrong,
                     f"mismatches: {wrong}" if wrong else "")
        report.check("exported PDF exists", bool(doc["storage_path"])
                     and Path(doc["storage_path"]).exists(), str(doc["storage_path"]))
        return report.summary()

    code = h.run_main(scenario)
    print(f"main() returned {code}")
    return code


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        build()
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "scan":
        sys.exit(scan())
    if len(sys.argv) > 1 and sys.argv[1] == "simulate":
        sys.exit(scan(simulate=True))
    print(__doc__)
    sys.exit(2)
