# DOK Scanner — batch document capture, auto-QC and indexing

A Windows desktop application for capturing batches of documents: it scans or imports them,
separates them into documents, cleans and quality-checks every page, reads the index fields
(including handwriting), lets staff correct what needs it, delivers a PDF with its index file, and
keeps a chain-of-custody record of physical boxes. An optional **central service** lets an
administrator manage projects for many PCs. Scanning works fully offline.

Current version: see `version.py` and `CHANGELOG.md`. Status, known limits and what is next:
`docs/RELEASE_NOTES.md` and `docs/ROADMAP.md`.

## Documents

| Read | If you are |
|---|---|
| `docs/OPERATOR_MANUAL.md` | scanning, checking and indexing documents every day |
| `docs/ADMIN_GUIDE.md` | managing users, projects, backups, the word lists, the central connection |
| `docs/INSTALL_GUIDE.md` | installing or upgrading on a PC; deploying the central service |
| `docs/CENTRAL_API.md` | calling the central service (generated from its code) |
| `central/README.md`, `central/deploy/DEPLOY.md` | running the central service |
| `docs/RELEASE_PROCESS.md` | making a release |
| `docs/INDEXING_SETUP.md` | setting up index fields for a new project |
| `docs/UAT-checklist.md`, `docs/DEMO_SCRIPT.md` | acceptance testing and the bank demo |
| `docs/GAP_ANALYSIS.md`, `docs/ROADMAP.md` | what is missing and the order to build it |
| `docs/security/` | threat model, SAST report, security testing scope |

## Stack

Python 3.11 · PyQt6 · OpenCV · numpy · Tesseract (printed text) · TrOCR via onnxruntime (handwriting) ·
pyzbar · img2pdf · reportlab · PyMuPDF · SQLite with SQLCipher · paramiko (SFTP) · pytwain (scanner).
Central service: FastAPI · SQLAlchemy · Alembic · PostgreSQL.

## Development setup

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt     # runtime + test + build tools
.\.venv\Scripts\python scripts\fetch_models.py           # the handwriting model (about 250 MB, not in git)
.\.venv\Scripts\python main.py                           # start the app (first sign-in: admin / admin, then you must change it)
```

Tesseract 5 must be on PATH for the OCR features (it is bundled in a release build).

| Task | Command |
|---|---|
| Quick tests (about 1.5 minutes, parallel, skips the slow real-OCR tests) | `python scripts\run_tests.py` |
| All tests (run before a release) | `python scripts\run_tests.py --all` |
| Lint and type checks | `ruff check .` and `mypy` (configured in `pyproject.toml`) |
| Look at every screen at a given window size | `python scripts\ui_snapshots.py DATA_COPY_DIR OUT_DIR 1280x720` |
| How well are fields read from real forms? | `python scripts\index_accuracy.py SAMPLE_DIR` |
| Central service tests | `python -m pytest tests\central` |

The same checks run in CI (`.github/workflows/ci.yml`).

## Layout

| Package | Purpose |
|---|---|
| `pipeline/` | the processing pipeline: job queue and its stages, hot folder, split/merge, validation |
| `projects/` | project settings: schema, versions, templates (`templates/*.json`), central overlay, repair |
| `security/` | users, roles, sign-in |
| `search/` | searching documents and exporting results |
| `sync/` | the connection to the central service: client, state, background sync, document reporting |
| `shared/` | small things every layer uses: safe file paths, data classes, the crash guard |
| `data/` | the encrypted SQLite layer, backup, key store, audit log |
| `scanning/` | scanners (TWAIN, WIA, vendor SDK stubs), file import, hot folder |
| `imaging/` | deskew, crop, enhancement, blank-page detection |
| `separation/` | barcode, blank-page and zone separation, the rule engine |
| `qc/` | the quality-control stages and score |
| `ocr/` | Tesseract wrapper, zone and box reading, handwriting reader and its corrections, document types |
| `export/` | PDF/TIFF writers, file names, index files |
| `delivery/` | local folder, SFTP, CD/DVD |
| `custody/` | chain-of-custody bundles and timeline |
| `ui/` | the PyQt6 screens and the event bus that carries pipeline events to them |
| `central/` | the central service (API, dashboard, migrations, deployment files) |
| `scripts/` | build, release, measuring and one-off tools |
| `installer/` | the Inno Setup script |

## Building

```powershell
.\.venv\Scripts\python scripts\release.py     # checks, tests, builds exe + installer, self-test, checksums
.\.venv\Scripts\python scripts\install_test.py   # silent install / uninstall on an empty profile
```

`release.py` needs Inno Setup 6. The release build bundles `tesseract.exe`, `tessdata` and
`libzbar-64.dll` when they are next to `docscanner.spec`; the handwriting model is copied from
`models\trocr` by the installer. The installer is **not yet code-signed**. Details:
`docs/RELEASE_PROCESS.md`.

## What is not done

See `docs/RELEASE_NOTES.md` (known limitations) and `docs/GAP_ANALYSIS.md`. In short: handwriting is read
well but not perfectly and needs the bank's own branch list; only some bank forms have defined field
positions; no automatic data retention; no code-signing certificate; multi-site consolidation, a mobile
client and Sinhala/Tamil recognition are not built. Phase 4 (an on-premise assistant and client web portal)
is out of scope for this release.

## Layering

Dependencies point one way: `ui` and `main.py` use `pipeline`, `search`, `sync`, `security` and `projects`;
`pipeline` uses the processing packages (`scanning`, `imaging`, `separation`, `qc`, `ocr`, `export`, `delivery`,
`custody`) and `projects`; those use only `shared` (and each other where they must). Nothing below `ui` imports
`ui`, and no processing package imports `pipeline`. `scripts/check_layers.py` enforces this (and CI runs it).
