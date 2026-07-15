# DOK Scanner — Batch Document Scanning · Auto-QC · Export Platform

Fully offline Windows desktop application for batch document scanning with a
5-stage automatic quality-control pipeline, OCR indexing, rule-based routing,
and multi-channel export. Built per `DOK_HLD_v2.docx` (Phase 1 — Core System).

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

## Packaging

PyInstaller one-file build via `docscanner.spec` (see HLD §21):

```powershell
.\.venv\Scripts\pyinstaller docscanner.spec --noconfirm
```

For a release build, place `tesseract.exe`, `tessdata/` and
`libzbar-64.dll` next to the spec first so OCR and barcode decoding work
on machines without those components installed.
