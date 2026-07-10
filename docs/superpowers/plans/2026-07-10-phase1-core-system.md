# DOK Batch Scanning Platform — Phase 1 Core System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Phase 1 core of the DOK Solutions batch document scanning, Auto-QC, and export platform per DOK_HLD_v2.docx — a fully offline PyQt6 Windows desktop app.

**Architecture:** Plugin-first, queue-based pipeline. Every extension point is a Python ABC (scanner sources, QC stages, delivery channels, rule actions). Documents flow through six named worker stages (ingest → OCR → enhancement → QC → rules → delivery) connected by `queue.Queue`, all workers in a shared `ThreadPoolExecutor`, UI thread never blocked. Full SQLite schema (all Phase 1–3 columns) created on first run; Phase 3 delivery channels shipped as stubs.

**Tech Stack:** Python 3.11 · PyQt6 · opencv-python · Pillow · numpy · pytesseract (Tesseract 5.5 system binary) · pyzbar · imagehash · img2pdf · reportlab · jsonschema · pytest.

## Global Constraints

- 100% offline — no network calls anywhere in the code (SFTP stub returns failure without connecting).
- SQLite schema complete from day 1 — tables: `projects`, `batches`, `documents`, `users`, `audit_log`, `jobs`; WAL mode; audit_log append-only.
- Every extension point behind an ABC: `BaseScannerSource`, `BaseQCStage`, `BaseDeliveryChannel`, `BaseRuleAction`.
- QC score: start 100, apply Appendix A deductions; PASS ≥ 80, WARN 60–79, FAIL ≤ 59.
- Original image always preserved — QC and enhancement operate on working copies.
- Project config = versioned JSON validated against a JSON Schema; filename template vars: `{date}`, `{barcode}`, `{counter:04d}`, `{field:<name>}`, `{document_type}`, `{batch_id}`.
- RBAC roles: admin / operator / qc_officer / viewer; every UI action checks `current_user.can(permission)`.
- All heavy work off the UI thread (`ThreadPoolExecutor` + queues).
- Layout follows HLD Section 16 exactly (top-level packages `ui/ core/ scanning/ imaging/ separation/ qc/ ocr/ export/ delivery/ data/`, `main.py` at root).
- Hardware-dependent code (TWAIN/WIA/pyzbar DLLs) must import lazily and degrade gracefully when the driver/DLL is absent.

---

### Task 1: Project scaffolding

**Files:** Create `requirements.txt`, `pytest.ini`, `.gitignore`, `README.md`, empty `__init__.py` in `core/ data/ scanning/ imaging/ separation/ qc/ ocr/ export/ delivery/ ui/ tests/`.

**Steps:**
- [ ] `git init`; create venv `.venv`; install deps: pyqt6 opencv-python pillow numpy pytesseract pyzbar imagehash img2pdf reportlab jsonschema pytest pytest-qt
- [ ] `pytest.ini` with `testpaths = tests`
- [ ] Sanity test `tests/test_scaffold.py`: imports every package. Run pytest → PASS. Commit.

### Task 2: Database layer

**Files:** Create `data/db.py`, `tests/test_db.py`.

**Interfaces (Produces):**
- `Database(path: str | Path)` — opens SQLite, enables WAL + foreign keys, runs `init_schema()` idempotently.
- `init_schema()` creates all 6 tables exactly per HLD §9 plus `jobs(job_id TEXT PK, batch_id, project_id, page_no INTEGER, status TEXT queued|in_progress|complete|failed, payload_path TEXT, created_at, updated_at)`.
- CRUD helpers: `insert_project/get_project/list_projects/update_project`, `insert_batch/update_batch_status`, `insert_document/update_document/get_document/list_documents(batch_id)`, `insert_user/get_user`, `append_audit(document_id, user, action, detail: dict)`, `upsert_job/get_incomplete_jobs/mark_job`.
- `documents` columns per HLD §9.3 incl. `qc_score`, `qc_detail`, `delivery_status` (default `'pending'`), `duplicate_of`, `audit_trail`.

**Tests:** schema creates all tables; second init is a no-op; document round-trip preserves JSON fields; audit rows append-only (no update helper exists); incomplete jobs returned after simulated crash.

### Task 3: Project configuration system

**Files:** Create `core/profile_manager.py`, `core/project_schema.json`, `tests/test_profile_manager.py`.

**Interfaces (Produces):**
- `ProfileManager(projects_dir, db)` with `create(name, config=None) -> dict`, `save(config) -> dict` (increments `version`, archives previous JSON to `projects/archive/<id>/v<N>.json`), `clone(project_id, new_name) -> dict` (new uuid, `version=1`, `cloned_from` set), `rollback(project_id, version) -> dict`, `load(project_id) -> dict`, `validate(config) -> None` (raises `ProjectConfigError` on schema violation).
- `DEFAULT_PROJECT` dict — full HLD §6.1 shape including disabled `sftp`/`cd_dvd` blocks and `features` toggles.
- `core/project_schema.json` — JSON Schema validating all top-level keys: scanner, enhancement_profile, separation, qc, ocr, export, delivery, features.

**Tests:** default validates; missing `qc.pass_score` fails; save bumps version + archives; clone resets version and sets `cloned_from`; rollback restores archived content.

### Task 4: Plugin ABCs

**Files:** Create `scanning/base.py`, `qc/base.py`, `delivery/base.py`, `separation/base.py`, `core/models.py`, `tests/test_abcs.py`.

**Interfaces (Produces):**
- `scanning/base.py`: `ScannerCapabilities` dataclass + `BaseScannerSource` ABC exactly per HLD §11.1 (`list_devices`, `connect`, `get_capabilities`, `acquire(dpi, color, duplex) -> List[bytes]`, `disconnect`).
- `qc/base.py`: `QCStageResult` dataclass (`stage: str, deduction: int, fixed: bool, flags: list[str], detail: dict, image: ndarray|None`) + `BaseQCStage` ABC with `name: str` property and `run(image: ndarray, context: dict) -> QCStageResult`.
- `delivery/base.py`: `DeliveryResult` dataclass + `BaseDeliveryChannel` ABC per HLD §12.1 (`name` property, `is_available()`, `deliver(doc_path, metadata) -> DeliveryResult`).
- `separation/base.py`: `BaseRuleAction` ABC — `action_type: str` property, `apply(document: dict, value) -> None`.
- `core/models.py`: `ScanJob` dataclass (`job_id, batch_id, project_id, page_no, image_path, source, created_at`) and `PageRecord`.

**Tests:** ABCs reject direct instantiation; minimal concrete subclasses work.

### Task 5: Rule engine JSON DSL

**Files:** Create `separation/rule_engine.py`, `separation/rule_schema.json`, `separation/actions.py`, `tests/test_rule_engine.py`.

**Interfaces (Produces):**
- `RuleEngine(rule_set: dict)` — validates against `rule_schema.json` at load; `evaluate(document: dict) -> list[dict]` runs every rule, applies matching actions via registered `BaseRuleAction` classes, returns list of applied action dicts.
- Condition ops: `eq neq contains not_contains is_empty is_not_empty gt lt gte lte matches_regex`; compound `AND OR NOT` (nesting allowed). Field paths dotted, e.g. `extracted_fields.invoice_no`.
- Actions in `separation/actions.py`: `set_folder set_filename set_validation_status add_flag set_metadata_field trigger_delivery skip_stage` — each a `BaseRuleAction` subclass registered in `ACTION_REGISTRY`.

**Tests:** each operator; nested AND(OR(...)); NOT; missing field → is_empty true / eq false; every action mutates document correctly; invalid rule set raises at load; HLD §8.1 example rule end-to-end.

### Task 6: RBAC skeleton

**Files:** Create `core/rbac.py`, `tests/test_rbac.py`.

**Interfaces (Produces):**
- `ROLE_PERMISSIONS: dict[str, set[str]]` for admin/operator/qc_officer/viewer per HLD §10.1. Permission strings: `scan.run`, `batch.view_own`, `batch.view_all`, `qc.review`, `project.edit`, `user.manage`, `report.view`, `report.export`, `config.backup`.
- `User` dataclass (`user_id, username, role`) with `can(permission: str) -> bool`.
- `Session` singleton-style holder: `login(username, password, db) -> User` (Phase 1: verifies against users table with sha256; seeds default `admin`/`admin` on first run), `current_user()`, `logout()`.

**Tests:** admin can everything; operator cannot `project.edit`; qc_officer cannot `scan.run`; viewer read-only; login seeds and authenticates default admin; bad password raises `AuthError`.

### Task 7: Image processing pipeline

**Files:** Create `imaging/deskew.py`, `imaging/crop.py`, `imaging/enhance.py`, `imaging/blank_detect.py`, `tests/test_imaging.py`.

**Interfaces (Produces):**
- `deskew.detect_skew_angle(img) -> float` (HoughLinesP median, ignore >±15°), `deskew.deskew(img) -> tuple[ndarray, float]` (warpAffine, white border fill).
- `crop.auto_crop(img, margin_px=40) -> ndarray` (findContours bbox + safe margin, never crops content).
- `enhance.apply_profile(img, profile: dict) -> ndarray` — CLAHE on LAB-L (clipLimit 2.0), background whitening (dilate+divide), despeckle (morphological open), driven by `enhancement_profile` toggles.
- `blank_detect.is_blank(img, threshold=0.02) -> bool` — ink-pixel density below threshold.

**Tests:** synthetic rotated text image deskews to <1° residual; crop preserves drawn content with margin; blank white page → True, text page → False; enhance respects toggles (disabled = unchanged pixels).

### Task 8: Document separation

**Files:** Create `separation/barcode.py`, `separation/blank_split.py`, `separation/zone_split.py`, `tests/test_separation.py`.

**Interfaces (Produces):**
- `barcode.decode_barcodes(img) -> list[dict]` (`{"type","value","rect"}`; lazy pyzbar import; returns `[]` with warning if DLL missing).
- `blank_split.split_on_blanks(pages: list[PageInfo], threshold) -> list[list[PageInfo]]` — blank page = boundary, removed after split.
- `zone_split.check_zone_trigger(img, zone: dict, ocr_fn) -> bool` — zone `{x,y,w,h,regex}`, OCR the region, regex match → boundary.
- `split_documents(pages, separation_cfg, ocr_fn) -> list[list[PageInfo]]` orchestrator honouring `strategy` array incl. `fixed_pages`.

**Tests:** blank-page split on synthetic sequence; fixed_pages N=2; barcode split path with monkeypatched decoder; zone trigger with fake ocr_fn; strategy combination.

### Task 9: OCR & indexing

**Files:** Create `ocr/tesseract_engine.py`, `ocr/zone_extractor.py`, `ocr/duplicate.py`, `tests/test_ocr.py`.

**Interfaces (Produces):**
- `tesseract_engine.ocr_page(img, lang='eng') -> OCRResult` (`text: str, confidence: float 0–1`); `ocr_osd(img) -> dict` for rotation stage; graceful `TesseractNotFound` if binary missing.
- `zone_extractor.extract_fields(img, zones: list[dict], lang) -> dict[str, FieldValue]` — zone `{name,x,y,w,h,regex?}`; regex-clean; `FieldValue(value, confidence)`.
- `duplicate.fingerprint(img_path) -> tuple[md5_hex, phash_hex]`; `duplicate.find_duplicate(fp, db, barcode=None) -> str|None` (exact MD5, pHash Hamming ≤ 5, barcode cross-check).

**Tests:** OCR of rendered text image contains the text (skip if tesseract absent); zone extraction with regex cleanup; identical file → MD5 dup; slightly-noised image → pHash dup.

### Task 10: Auto-QC pipeline — 5 stages + scorer

**Files:** Create `qc/punch_hole.py`, `qc/rotation.py`, `qc/margin.py`, `qc/color.py`, `qc/advanced.py`, `qc/scorer.py`, `tests/test_qc_stages.py`, `tests/test_scorer.py`.

**Interfaces (Produces):** each stage a `BaseQCStage` subclass with `name` in `punch_hole|rotation|margin|color|advanced`; `run(image, context)` per HLD §5 methods/deductions (Appendix A values). `context` carries project qc config + prior stage output.
- `scorer.score_page(results: list[QCStageResult], pass_score=80) -> QCVerdict` (`score: int` clamped 0–100, `grade: PASS|WARN|FAIL`, `detail: dict per-stage`). Auto-FAIL conditions (blur < fail threshold, DPI < 150) force grade FAIL regardless of arithmetic.
- `qc/pipeline.py` helper `run_qc(image, config, dpi) -> tuple[ndarray, QCVerdict]` running enabled stages in configured order on a working copy.

**Tests:** scorer boundaries (100→PASS, 79→WARN, 59→FAIL, clamp at 0); deduction table honoured; skip-stage honoured via `qc.stages`; punch-hole synthetic circles detected & inpainted; blur stage flags Gaussian-blurred image; margin DPI warn/fail; color stage improves Michelson contrast on low-contrast synthetic.

### Task 11: Queue-based pipeline & job recovery

**Files:** Create `core/job_queue.py`, `tests/test_pipeline.py`.

**Interfaces (Produces):**
- `Pipeline(db, profile, channels, executor=None)` — six stages: `ingest_q → ocr_q → enhance_q → qc_q → rule_q → delivery_q`, workers `ScanWorker OCRWorker(xN) EnhancementWorker QCWorker RuleWorker DeliveryWorker` consuming/producing between queues.
- `submit(job: ScanJob)` persists to `jobs` table (`queued`) before enqueue; each worker marks `in_progress`/`complete`/`failed`.
- `recover(db) -> list[ScanJob]` re-queues `status IN ('queued','in_progress')` on startup.
- `Pipeline.events` — thin observer (`subscribe(fn)`) emitting `{stage, job_id, status}` dicts for the UI job monitor.
- `shutdown(wait=True)` drains queues and stops workers with sentinels.

**Tests:** single job traverses all six stages and lands `delivery_status='delivered_local'`; QC FAIL routes to review (document `validation_status='pending_qc'`, not delivered); crash simulation (kill before complete) → `recover()` returns job; events fire per stage.

### Task 12: Export engine

**Files:** Create `export/pdf_writer.py`, `export/tiff_writer.py`, `export/batch_exporter.py`, `export/filename.py`, `tests/test_export.py`.

**Interfaces (Produces):**
- `filename.render(template: str, ctx: dict) -> str` supporting `{date}`, `{barcode}`, `{counter:04d}`, `{field:invoice_no}`, `{document_type}`, `{batch_id}`; unsafe chars sanitised.
- `pdf_writer.write_image_pdf(images: list[Path], out: Path)` (img2pdf); `pdf_writer.write_searchable_pdf(images, ocr_results, out, pdf_a=False)` (reportlab invisible text layer).
- `tiff_writer.write_multipage_tiff(images, out, compression='tiff_lzw'|'group4')`.
- `batch_exporter.BatchExporter(executor)` — `export_document(doc, images, cfg) -> Future`, retries 3× on failure then flags.

**Tests:** template rendering incl. counter padding and missing-field fallback; image PDF valid & page count right; searchable PDF contains text layer; multipage TIFF page count; retry logic with flaky writer (fails twice, succeeds third).

### Task 13: Delivery channels

**Files:** Create `delivery/local_folder.py`, `delivery/sftp_channel.py`, `delivery/cddvd_channel.py`, `delivery/registry.py`, `tests/test_delivery.py`.

**Interfaces (Produces):**
- `LocalFolderChannel(cfg)` — copy + MD5 verify, returns `DeliveryResult(success=True, checksum=...)`; creates dirs.
- `SFTPChannel`/`CDDVDChannel` stubs: `is_available() -> False`, `deliver()` logs "not yet enabled", returns `DeliveryResult(success=False)`. No paramiko import in Phase 1.
- `registry.build_channels(delivery_cfg) -> list[BaseDeliveryChannel]` — all three registered, only enabled+available ones invoked by DeliveryWorker; maps channel→`delivery_status` value (`delivered_local`/`delivered_sftp`/`burned_to_disc`).

**Tests:** local delivery copies + checksum matches; corrupted copy → success False; stubs return failure without network; registry respects enabled flags.

### Task 14: Audit logging

**Files:** Create `data/audit.py`, `tests/test_audit.py`.

**Interfaces (Produces):** `AuditLogger(db, log_dir)` — `log(action, user, document_id=None, **detail)` writes DB row via `db.append_audit` AND rotating file (`logging.handlers.RotatingFileHandler`, 5 MB × 5); also appends to the document's `audit_trail` JSON when document_id given.

**Tests:** DB row + file line written; document audit_trail grows; rotation config sane.

### Task 15: Scan sources

**Files:** Create `scanning/folder_watcher.py`, `scanning/file_import.py`, `scanning/twain_source.py`, `scanning/wia_source.py`, `tests/test_scanning.py`.

**Interfaces (Produces):**
- `file_import.import_files(paths, batch_id, project_id, staging_dir) -> list[ScanJob]` — accepts PDF/TIFF/JPG/PNG/BMP; multi-page PDFs/TIFFs exploded to per-page PNGs in staging.
- `FolderWatchSource(watch_dir, callback)` — polling-based observer (2 s interval, stdlib only; watchdog optional), `start()/stop()`, fires callback with new stable files (size unchanged between polls).
- `TWAINSource`/`WIASource(BaseScannerSource)` — lazy imports; `list_devices()` returns `[]` and `connect` raises `ScannerUnavailable` when the driver stack is absent; WIA via `win32com.client` COM when available.

**Tests:** file import explodes a 3-page TIFF into 3 jobs; folder watcher detects new file and ignores growing file until stable; scanner sources degrade gracefully without hardware.

### Task 16: PyQt6 UI

**Files:** Create `ui/main_window.py`, `ui/login_dialog.py`, `ui/scan_wizard.py`, `ui/image_viewer.py`, `ui/qc_review.py`, `ui/index_editor.py`, `ui/job_monitor.py`, `core/event_bus.py`, `tests/test_ui_smoke.py`.

**Interfaces (Produces):**
- `event_bus.EventBus(QObject)` — signals: `job_progress(dict)`, `qc_ready(str)`, `batch_complete(str)`; single shared instance.
- `LoginDialog` → authenticates via `core.rbac.Session.login`.
- `MainWindow(db, profile_manager, bus)` — QTabWidget: Scan / QC Review / Index / Job Monitor (+ Projects tab admin-only via `user.can('project.edit')`).
- `ScanWizard(QWizard)` — pages: select project → select source (file import / hot folder / scanner) → confirm & start; emits `ScanJob`s into pipeline.
- `ImageViewer(QGraphicsView)` — zoom (wheel), pan, fit; thumbnail strip `QListWidget`.
- `QCReviewPanel` — before/after `ImageViewer` pair, WARN/FAIL queue list, Accept/Reject/Rescan buttons updating `validation_status` + audit.
- `IndexEditor` — `QFormLayout` from project ocr zones; `QCompleter` fed by SQLite distinct values.
- `JobMonitor` — `QTableWidget` fed by `EventBus.job_progress`; per-stage progress bars.
- Role gating: buttons disabled when `not user.can(...)`.

**Tests (pytest-qt, offscreen):** each widget constructs; login gate blocks bad credentials; role gating disables admin tab for operator; job monitor row appears on event.

### Task 17: Entry point, integration test, packaging

**Files:** Create `main.py`, `docscanner.spec`, `tests/test_integration.py`, update `README.md`.

**Interfaces:** `main.py` — creates `%LOCALAPPDATA%/DOKScanner` data dir, opens `Database`, seeds admin, `QApplication` with `Fusion` style, LoginDialog → MainWindow, starts `Pipeline` + `recover()`.
**Integration test (headless):** synthesise a 4-page batch (2 docs split by blank page, one page blurred), run through full pipeline with LocalFolderChannel to temp dir → assert: 2 PDFs exported for PASS docs, blurred page in review queue, documents table rows correct, audit_log populated, jobs all `complete`.
**Packaging:** PyInstaller spec per HLD §21.1 (documented; build optional in dev environment).

---

## Verification

- `pytest` full suite green.
- `python main.py` launches to login (manual/offscreen smoke).
- Integration test proves end-to-end flow: import → separate → OCR → QC → rules → export → deliver → audit.
