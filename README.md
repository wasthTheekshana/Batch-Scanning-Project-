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

## Packaging

PyInstaller one-file build via `docscanner.spec` (see HLD §21).
