# DOK Scanner — Batch Document Scanning · Auto-QC · Export Platform

Fully offline Windows desktop application for batch document scanning with a
5-stage automatic quality-control pipeline, OCR indexing, rule-based routing,
and multi-channel export. Built per `DOK_HLD_v2.docx` (Phases 1–3, in
progress toward go-live).

## Stack

Python 3.11 · PyQt6 · OpenCV · Pillow · numpy · pytesseract · pyzbar ·
img2pdf · reportlab · SQLite (WAL)

## Development setup

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python -m pytest        # run test suite
.\.venv\Scripts\python main.py          # launch the app (default login: admin / admin)
```

Tesseract 5 must be on PATH for OCR features (bundled in the packaged EXE).

## Layout (HLD §16)

| Package | Purpose |
|---|---|
| `core/` | job queue pipeline, project config, RBAC, event bus, models |
| `data/` | SQLite layer, audit logging |
| `scanning/` | scanner sources (TWAIN/WIA), file import, hot folder |
| `imaging/` | deskew, crop, enhance, blank detection |
| `separation/` | barcode/blank/zone splitters, rule engine JSON DSL |
| `qc/` | 5 QC stages + score engine |
| `ocr/` | Tesseract wrapper, zone field extraction, duplicate detection |
| `export/` | PDF/TIFF writers, filename templates, batch exporter |
| `delivery/` | local folder channel + SFTP/CD-DVD stubs (Phase 3) |
| `ui/` | PyQt6 panels: scan wizard, viewer, QC review, index editor, job monitor |
| `custody/` | bundle/box chain-of-custody tracking (Enhancement Plan §3.2) |

## Phase status

- **Phase 1 (core)** — complete: pipeline, 5-stage QC, OCR, export, local delivery, UI.
- **Phase 2 (enhanced)** — complete: bcrypt auth, DPAPI keystore, SQLCipher
  encrypted DB (auto-migrates a plaintext Phase 1 DB on startup), encrypted
  scheduled backups with restore, admin panel, workflow designer, vendor SDK
  scaffolding (drop vendor DLLs into `vendor_sdks/`).
- **Phase 3 (new modules)** — complete: real SFTP delivery (paramiko, LAN
  only, retry + size verification), CD/DVD burn queue with checksum verify
  and disc log (configure `burner_cmd` for ImgBurn/cdrecord), multi-channel
  delivery incl. rule-triggered channels, benchmark suite
  (`python scripts/benchmark.py --pages 100 [--ocr]`; dev machine:
  141 pages/min no-OCR, 115 pages/min with OCR).
- **Layer 5 indexing (HLD §4)** — complete: indexing work queue with page
  preview, zone-based auto-extract that searches every page and scales
  resolution-independently, live zone-test/regex diagnosis in the workflow
  designer (PDF or image samples, 300 DPI), and lookup-table validation
  of extracted values against previously-indexed history.
- **QC & reporting** — complete: manual page editor (rotate/crop/whiteout/
  enhance, audit-logged), QC review "All documents" filter, reports tab
  with QC report PDF export.
- **Chain-of-custody tracking** — complete: QR-coded bundle label
  sheets, auto-logged scanning checkpoint on scan, manual receipt/prep/
  QC/storage/return checkpoints, reconciliation view with mismatch
  flagging, and an escalation hook for damaged/missing/compromised
  bundles (`docs/DOK_Scanner_Enhancement_Plan (1).docx` §3.2).
- **Project templates** — complete: five built-in starter templates
  (Banking, Insurance, HR/Personnel, Legal/Contracts, Invoices) plus
  "Save as template" so admins can turn any configured project into a
  reusable starting point for the next one
  (`docs/DOK_Scanner_Enhancement_Plan (1).docx` §4.4, generalized beyond
  banking-only per project direction).
- **QC strictness profiles** — complete: a one-click Strict/Standard/
  Lenient preset in the Workflow Designer sets pass score, blur, and DPI
  thresholds together for document sensitivity (e.g. legal/financial vs
  routine correspondence); DPI WARN/FAIL thresholds are now editable in
  the UI (previously only configurable via the raw project JSON)
  (`docs/DOK_Scanner_Enhancement_Plan (1).docx` §4.3 — the "sample QC
  report format" half of this item is already satisfied by the existing
  Reports tab's per-document QC score/status export).
- **Remaining before go-live** — bundle `tesseract.exe`/`tessdata`/
  `libzbar-64.dll` into the release build (blocked on binaries), vendor
  SDK `acquire()` bodies for Kodak/Alaris/CZUR/Viisan (blocked on vendor
  DLLs), code-signing cert, Inno Setup installer compile + verify, client
  UAT sign-off (`docs/UAT-checklist.md`).
- Phase 4 (on-premise LLM assistant + client web portal) is out of scope
  per the HLD and deferred to a future release.

## Packaging

PyInstaller one-file build via `docscanner.spec` (see HLD §21):

```powershell
.\.venv\Scripts\pyinstaller docscanner.spec --noconfirm
```

For a release build, place `tesseract.exe`, `tessdata/` and
`libzbar-64.dll` next to the spec first so OCR and barcode decoding work
on machines without those components installed.
