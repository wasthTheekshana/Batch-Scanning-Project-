# Full-Page OCR Auto-Indexing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `ocr.mode = "full_page"` extraction path so index fields can be located by a label-anchored regex against the whole OCR'd page, as an alternative to today's fixed-rectangle zone cropping — without touching zone-based extraction's existing behavior.

**Architecture:** A new pure function `extract_fields_from_text()` in `ocr/zone_extractor.py` mirrors the existing `extract_fields()`'s regex/FieldValue contract but operates on already-OCR'd page text instead of cropping+OCR'ing per zone. `core/job_queue.py::_extract_zone_fields` branches on `profile["ocr"]["mode"]` to call one or the other, reusing the whole-page OCR result (`record.ocr_text`/`record.ocr_confidence`) that's already computed earlier in the same pipeline stage. The Workflow Designer UI gains a per-project mode toggle, and `ZoneDialog` adapts (no drag-region requirement, tests against the whole sample) when editing a full-page-mode zone.

**Tech Stack:** Python, PyQt6, pytest — matches the rest of the codebase, no new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-29-full-page-ocr-indexing-design.md`

## Global Constraints

- `ocr.mode` is optional; its absence must mean `"zone"` (today's behavior) everywhere it's read — every existing project/template must work unchanged with zero migration.
- No second OCR call in full-page mode — reuse `record.ocr_text`/`record.ocr_confidence`, already populated by `_ocr_worker` before `_extract_zone_fields` runs (`core/job_queue.py:235,239`).
- A full-page-mode zone with no `regex` is skipped with a `log.warning`, never raises.
- `FieldValue` (`value: str`, `confidence: float`) stays the single return shape for both extraction paths — nothing downstream of `_extract_zone_fields` changes.

---

### Task 1: `extract_fields_from_text()` in the extraction module

**Files:**
- Modify: `ocr/zone_extractor.py`
- Test: `tests/test_ocr.py`

**Interfaces:**
- Consumes: nothing new — uses the existing `FieldValue` dataclass already defined in `ocr/zone_extractor.py`.
- Produces: `extract_fields_from_text(text: str, confidence: float, zones: list[dict]) -> dict[str, FieldValue]` — called by Task 2.

- [x] **Step 1: Write the failing tests**

Add to `tests/test_ocr.py`, in the `# ---- zone extraction` section, right after `test_extract_fields_skips_zone_missing_geometry`:

```python
def test_extract_fields_from_text_matches_label_anchor():
    from ocr.zone_extractor import extract_fields_from_text
    text = "Some header text\nInvoice No: INV-7777\nVendor: ACME Corp\nfooter"
    zones = [{"name": "invoice_no", "regex": r"Invoice No:\s*(\S+)"},
             {"name": "vendor", "regex": r"Vendor:\s*(.+)"}]
    fields = extract_fields_from_text(text, 0.87, zones)
    assert fields["invoice_no"].value == "INV-7777"
    assert fields["invoice_no"].confidence == 0.87
    assert fields["vendor"].value == "ACME Corp"
    assert fields["vendor"].confidence == 0.87


def test_extract_fields_from_text_no_match_yields_zero_confidence():
    from ocr.zone_extractor import extract_fields_from_text
    zones = [{"name": "invoice_no", "regex": r"Invoice No:\s*(\S+)"}]
    fields = extract_fields_from_text("nothing relevant here", 0.9, zones)
    assert fields["invoice_no"].value == ""
    assert fields["invoice_no"].confidence == 0.0


def test_extract_fields_from_text_skips_zone_without_regex():
    from ocr.zone_extractor import extract_fields_from_text
    zones = [{"name": "unanchored"},
             {"name": "invoice_no", "regex": r"Invoice No:\s*(\S+)"}]
    fields = extract_fields_from_text("Invoice No: INV-1", 0.9, zones)
    assert "unanchored" not in fields
    assert fields["invoice_no"].value == "INV-1"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ocr.py -k extract_fields_from_text -v`
Expected: FAIL with `ImportError` / `AttributeError: module 'ocr.zone_extractor' has no attribute 'extract_fields_from_text'`

- [x] **Step 3: Implement `extract_fields_from_text`**

Add to `ocr/zone_extractor.py`, after `extract_fields()`:

```python
def extract_fields_from_text(text: str, confidence: float,
                             zones: list[dict]) -> dict[str, FieldValue]:
    """Full-page mode: locate each field's value in already-OCR'd page
    text via its label-anchored regex, instead of cropping a fixed zone.
    A zone with no regex is skipped — unanchored, page-wide matching has
    no way to know which part of the page belongs to it."""
    fields: dict[str, FieldValue] = {}
    for zone in zones:
        pattern = zone.get("regex")
        if not pattern:
            continue
        m = re.search(pattern, text)
        if m:
            value = m.group(1) if m.lastindex else m.group(0)
            fields[zone["name"]] = FieldValue(value=value, confidence=confidence)
        else:
            fields[zone["name"]] = FieldValue(value="", confidence=0.0)
    return fields
```

(`re` is already imported at the top of `ocr/zone_extractor.py`.)

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ocr.py -k extract_fields_from_text -v`
Expected: PASS (3 tests)

- [x] **Step 5: Run the full OCR test file to confirm no regressions**

Run: `pytest tests/test_ocr.py -v`
Expected: PASS (all tests, `test_ocr_page_reads_rendered_text` skipped if tesseract isn't on PATH)

- [x] **Step 6: Commit**

```bash
git add ocr/zone_extractor.py tests/test_ocr.py
git commit -m "feat(ocr): add extract_fields_from_text for full-page field matching"
```

---

### Task 2: Wire full-page mode into the pipeline

**Files:**
- Modify: `core/project_schema.json` (ocr.mode enum)
- Modify: `core/job_queue.py:245-269` (`_extract_zone_fields`)
- Test: `tests/test_auto_index.py`

**Interfaces:**
- Consumes: `extract_fields_from_text(text, confidence, zones)` from Task 1.
- Produces: `_extract_zone_fields` now reads `ocr_cfg.get("mode", "zone")`; no change to its own signature or to what it writes onto `record` (`record.extracted_fields`, `record.low_conf_fields` — unchanged shape, consumed downstream unchanged).

- [x] **Step 1: Add `mode` to the OCR schema**

In `core/project_schema.json`, find the `"ocr"` block (currently):

```json
    "ocr": {
      "type": "object",
      "required": ["lang"],
      "properties": {
        "lang": {"type": "string"},
        "zones": {"type": "array"},
        "confidence_threshold": {"type": "number", "minimum": 0, "maximum": 1}
      }
    },
```

Change to:

```json
    "ocr": {
      "type": "object",
      "required": ["lang"],
      "properties": {
        "lang": {"type": "string"},
        "mode": {"type": "string", "enum": ["zone", "full_page"]},
        "zones": {"type": "array"},
        "confidence_threshold": {"type": "number", "minimum": 0, "maximum": 1}
      }
    },
```

- [x] **Step 2: Write the failing pipeline test**

Add to `tests/test_auto_index.py`, after `test_zone_fields_populate_extracted_fields`:

```python
def fake_ocr_full_page(image):
    """Whole-page OCR stand-in used by full-page-mode tests: returns a
    fixed page of text regardless of the image, same shape (text,
    confidence) as ocr/tesseract_engine.OCRResult."""
    return OCRResult(
        text="Header stuff\nInvoice No: INV-7777\nfooter",
        confidence=0.88)


def test_full_page_mode_populates_extracted_fields(tmp_path, db):
    profile = make_profile(tmp_path)
    profile["features"]["auto_index"] = True
    profile["ocr"]["mode"] = "full_page"
    profile["ocr"]["zones"] = [
        {"name": "invoice_no", "regex": r"Invoice No:\s*(\S+)"}]
    profile["ocr"]["confidence_threshold"] = 0.75
    from core.job_queue import Pipeline
    from delivery.local_folder import LocalFolderChannel
    from export.batch_exporter import export_document
    pipeline = Pipeline(
        db, profile,
        [LocalFolderChannel(profile["delivery"]["local_folder"])],
        export_document, ocr_fn=fake_ocr_full_page,
        working_dir=tmp_path / "staging")
    pipeline.start()
    try:
        jobs = write_pages(tmp_path, ["text"])
        for j in jobs:
            j.batch_id = "batch-fp"
        pipeline.submit_batch("batch-fp", jobs)
        assert wait_for(lambda: len(db.list_documents("batch-fp")) >= 1
                        and all(d["validation_status"] != "pending_qc"
                                or d["qc_detail"]
                                for d in db.list_documents("batch-fp")))
    finally:
        pipeline.shutdown()
    docs = db.list_documents("batch-fp")
    fields = json.loads(docs[0]["extracted_fields"])
    assert fields["invoice_no"] == "INV-7777"


def test_full_page_mode_low_confidence_flagged(tmp_path, db):
    profile = make_profile(tmp_path)
    profile["features"]["auto_index"] = True
    profile["ocr"]["mode"] = "full_page"
    profile["ocr"]["zones"] = [
        {"name": "invoice_no", "regex": r"Invoice No:\s*(\S+)"}]
    profile["ocr"]["confidence_threshold"] = 0.99  # above fake's 0.88
    from core.job_queue import Pipeline
    from delivery.local_folder import LocalFolderChannel
    from export.batch_exporter import export_document
    pipeline = Pipeline(
        db, profile,
        [LocalFolderChannel(profile["delivery"]["local_folder"])],
        export_document, ocr_fn=fake_ocr_full_page,
        working_dir=tmp_path / "staging")
    pipeline.start()
    try:
        jobs = write_pages(tmp_path, ["text"])
        for j in jobs:
            j.batch_id = "batch-fp2"
        pipeline.submit_batch("batch-fp2", jobs)
        assert wait_for(lambda: len(db.list_documents("batch-fp2")) >= 1
                        and all(d["validation_status"] != "pending_qc"
                                or d["qc_detail"]
                                for d in db.list_documents("batch-fp2")))
    finally:
        pipeline.shutdown()
    docs = db.list_documents("batch-fp2")
    detail = json.loads(docs[0]["qc_detail"])
    assert "low_confidence_index" in detail["flags"]
```

- [x] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_auto_index.py -k full_page -v`
Expected: FAIL — `test_full_page_mode_populates_extracted_fields` fails because `fields["invoice_no"]` isn't populated (mode isn't read yet, so `_extract_zone_fields` still runs zone-based `extract_fields` against zones that have no `x/y/w/h`, extracting nothing).

- [x] **Step 4: Implement the branch in `_extract_zone_fields`**

In `core/job_queue.py`, replace lines 245-262 (`_extract_zone_fields`, up through the `try:`/`except` block that calls `extract_fields`):

```python
    def _extract_zone_fields(self, record: PageRecord, image) -> None:
        """Auto-index: extract configured OCR zones into page fields
        (HLD §4 Layer 5). Values below the confidence threshold are kept
        but flagged so the operator can correct them in the index editor.
        ocr.mode selects zone-based extraction (fixed pixel regions,
        cropped and re-OCR'd) or full-page extraction (label-anchored
        regex against the page text already OCR'd for record.ocr_text)."""
        ocr_cfg = self.profile.get("ocr", {})
        zones = ocr_cfg.get("zones", [])
        if not zones or not self.profile.get("features", {}).get("auto_index"):
            return
        threshold = float(ocr_cfg.get("confidence_threshold", 0.75))
        mode = ocr_cfg.get("mode", "zone")
        try:
            if mode == "full_page":
                from ocr.zone_extractor import extract_fields_from_text
                fields = extract_fields_from_text(
                    record.ocr_text, record.ocr_confidence, zones)
            else:
                from ocr.zone_extractor import extract_fields
                fields = extract_fields(image, zones,
                                        lang=ocr_cfg.get("lang", "eng"),
                                        ocr_fn=self.ocr_fn)
        except Exception as e:
            log.warning("zone extraction failed for page %s: %s",
                        record.job.page_no, e)
            return
```

(The `for name, fv in fields.items():` loop below, lines 263-269, is unchanged — leave it as-is.)

- [x] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_auto_index.py -v`
Expected: PASS (all tests, including the 2 new full-page ones and all pre-existing zone-mode ones)

- [x] **Step 6: Run the schema-validating tests to confirm the new enum doesn't break existing configs**

Run: `pytest tests/test_templates.py tests/test_pipeline.py -v`
Expected: PASS

- [x] **Step 7: Commit**

```bash
git add core/project_schema.json core/job_queue.py tests/test_auto_index.py
git commit -m "feat(ocr): route ocr.mode=full_page through label-anchored extraction"
```

---

### Task 3: `ZoneDialog` full-page editing mode

**Files:**
- Modify: `ui/zone_dialog.py`
- Test: `tests/test_index_tab.py`

**Interfaces:**
- Consumes: `ocr_page` / `TesseractNotFound` from `ocr/tesseract_engine.py` (already imported in this file).
- Produces: `ZoneDialog(zone: dict | None = None, parent=None, full_page: bool = False)`. `build_zone()` returns `{"name": ..., "regex": ...}` (no `x/y/w/h/ref_*`) when `full_page=True` and the name field is non-empty — this is what Task 4's Workflow Designer wiring appends to `cfg["ocr"]["zones"]`.

- [x] **Step 1: Write the failing tests**

Add to `tests/test_index_tab.py`, near the other `ZoneDialog` tests (around line 282's `test_zone_dialog_builds_zone`). Each test imports `ZoneDialog` locally, matching this file's existing pattern:

```python
def test_zone_dialog_full_page_build_zone_without_region(qtbot):
    from ui.zone_dialog import ZoneDialog
    dlg = ZoneDialog(full_page=True)
    qtbot.addWidget(dlg)
    dlg.name.setText("invoice_no")
    dlg.regex.setText(r"Invoice No:\s*(\S+)")
    zone = dlg.build_zone()
    assert zone == {"name": "invoice_no", "regex": r"Invoice No:\s*(\S+)"}


def test_zone_dialog_full_page_disables_region_controls(qtbot):
    from ui.zone_dialog import ZoneDialog
    dlg = ZoneDialog(full_page=True)
    qtbot.addWidget(dlg)
    assert not dlg.x.isEnabled()


def test_zone_dialog_zone_mode_still_requires_region(qtbot):
    from ui.zone_dialog import ZoneDialog
    dlg = ZoneDialog()  # full_page defaults to False
    qtbot.addWidget(dlg)
    dlg.name.setText("invoice_no")
    assert dlg.build_zone() is None  # no region dragged, w/h are 0
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_index_tab.py -k full_page -v`
Expected: FAIL — `TypeError: ZoneDialog.__init__() got an unexpected keyword argument 'full_page'`

- [x] **Step 3: Implement `full_page` support in `ZoneDialog`**

In `ui/zone_dialog.py`, change the constructor signature (currently `def __init__(self, zone: dict | None = None, parent=None):`, line 38) to:

```python
    def __init__(self, zone: dict | None = None, parent=None,
                full_page: bool = False):
        super().__init__(parent)
        self.full_page = full_page
        self.setWindowTitle("Index zone" + (" (full-page)" if full_page else ""))
```

(Keep the rest of `__init__` as-is until after the coords row is built.) After the coords `QHBoxLayout` is fully assembled and `form.addRow("Region (px)", coords)` runs (around line 70), add:

```python
        if full_page:
            for sb in (self.x, self.y, self.w, self.h):
                sb.setEnabled(False)
            self.hint_full_page = QLabel(
                "Full-page mode: no region needed. The regex is matched "
                "against the whole page's OCR text — write it with a "
                "label anchor, e.g. r\"Invoice No:\\s*(\\S+)\".")
            self.hint_full_page.setWordWrap(True)
            layout.addWidget(self.hint_full_page)
```

(`layout` is the outer `QVBoxLayout` already in scope at that point in `__init__`.)

Update `suggest_regex`'s button (around line 81-86) to disable itself in full-page mode — right after `self.suggest_btn.clicked.connect(self.suggest_regex)`:

```python
        if full_page:
            self.suggest_btn.setEnabled(False)
            self.suggest_btn.setToolTip(
                "Not available in full-page mode — its \"Label : value\" "
                "heuristic assumes a tight crop around one field.")
```

Update `_read_region` (line 181) so full-page mode reads the whole sample instead of requiring a dragged region — replace the existing body with:

```python
    def _read_region(self):
        """Run real OCR on the configured region of the loaded sample
        (or the whole sample, in full-page mode). Returns None (and sets
        self.result with why) on any failure."""
        if self._sample is None:
            self.result.setText("Load a sample page first.")
            return None
        if self.full_page:
            region = self._sample
        else:
            if not self.w.value() or not self.h.value():
                self.result.setText("Drag a region on the sample first.")
                return None
            h, w = self._sample.shape[:2]
            x0 = max(0, min(self.x.value(), w - 1))
            y0 = max(0, min(self.y.value(), h - 1))
            x1 = max(x0 + 1, min(x0 + self.w.value(), w))
            y1 = max(y0 + 1, min(y0 + self.h.value(), h))
            region = self._sample[y0:y1, x0:x1]
        try:
            from ocr.tesseract_engine import TesseractNotFound, ocr_page
            return ocr_page(region)
        except TesseractNotFound:
            self.result.setText("Tesseract OCR is not installed on this "
                                "machine — cannot test here, but the zone "
                                "will work on machines with OCR.")
            return None
        except Exception as e:
            self.result.setText(f"OCR failed: {e}")
            return None
```

Finally, update `build_zone` (line 255) to skip the region requirement in full-page mode:

```python
    def build_zone(self) -> dict | None:
        name = self.name.text().strip()
        if not name:
            return None
        if self.full_page:
            zone = {"name": name}
            if self.regex.text().strip():
                zone["regex"] = self.regex.text().strip()
            return zone
        if self.w.value() == 0 or self.h.value() == 0:
            return None
        zone = {"name": name, "x": self.x.value(), "y": self.y.value(),
                "w": self.w.value(), "h": self.h.value()}
        if self._sample is not None:
            zone["ref_h"], zone["ref_w"] = self._sample.shape[:2]
        elif self._ref[0] and self._ref[1]:
            zone["ref_w"], zone["ref_h"] = self._ref
        if self.regex.text().strip():
            zone["regex"] = self.regex.text().strip()
        return zone
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_index_tab.py -v`
Expected: PASS (all tests, including the 3 new ones and every pre-existing `ZoneDialog` test, which all use `full_page=False` by default)

- [x] **Step 5: Commit**

```bash
git add ui/zone_dialog.py tests/test_index_tab.py
git commit -m "feat(ui): ZoneDialog supports full-page editing (no region required)"
```

---

### Task 4: Workflow Designer mode toggle

**Files:**
- Modify: `ui/workflow_designer.py`
- Test: `tests/test_phase2_ui.py`

**Interfaces:**
- Consumes: `ZoneDialog(zone, parent=self, full_page=bool)` from Task 3.
- Produces: `cfg["ocr"]["mode"]` written by `collect_config()`, read by `_bind_config()` — this is what Task 2's pipeline reads at runtime once a project using this UI is saved.

- [x] **Step 1: Write the failing test**

`tests/test_phase2_ui.py` already has a `designer` fixture (near line 57) that builds a real `WorkflowDesigner(db, pm, ADMIN)` bound to a freshly-created project — reuse it. Add this test in the `# ---- workflow designer` section, near `test_designer_loads_project`:

```python
def test_designer_ocr_mode_roundtrip(designer):
    assert designer.ocr_mode.currentText() == "Zone-based (draw regions)"
    designer.ocr_mode.setCurrentText("Full-page (label-anchored regex)")
    cfg = designer.collect_config()
    assert cfg["ocr"]["mode"] == "full_page"
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phase2_ui.py -k ocr_mode_roundtrip -v`
Expected: FAIL — `AttributeError: 'WorkflowDesigner' object has no attribute 'ocr_mode'`

- [x] **Step 3: Add the mode combo box**

In `ui/workflow_designer.py`, in the zone box construction (currently lines 300-304):

```python
        # index zones (auto-index fields)
        zone_box = QGroupBox("Index zones (auto-index fields)")
        z_layout = QVBoxLayout(zone_box)
        z_layout.addWidget(QLabel(
            "Each zone reads one field from a region of the page. "
            "Add a zone, load a sample page, and drag the region."))
```

change to:

```python
        # index zones (auto-index fields)
        zone_box = QGroupBox("Index zones (auto-index fields)")
        z_layout = QVBoxLayout(zone_box)
        z_layout.addWidget(QLabel(
            "Each zone reads one field from the page. Zone-based: drag a "
            "fixed region per field. Full-page: OCR the whole page and "
            "match each field by a label-anchored regex — use this when "
            "field position varies between documents in this project."))
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Extraction mode"))
        self.ocr_mode = QComboBox()
        self.ocr_mode.addItems(["Zone-based (draw regions)",
                                "Full-page (label-anchored regex)"])
        self.ocr_mode.setEnabled(self.can_edit)
        mode_row.addWidget(self.ocr_mode)
        mode_row.addStretch(1)
        z_layout.addLayout(mode_row)
```

- [x] **Step 4: Bind mode to config on load and save**

In `_bind_config` (`ui/workflow_designer.py`), find:

```python
        self.zone_list.clear()
        for zone in cfg.get("ocr", {}).get("zones", []):
            self._append_zone(zone)
```

and change to:

```python
        self.ocr_mode.blockSignals(True)
        self.ocr_mode.setCurrentText(
            "Full-page (label-anchored regex)"
            if cfg.get("ocr", {}).get("mode") == "full_page"
            else "Zone-based (draw regions)")
        self.ocr_mode.blockSignals(False)
        self.zone_list.clear()
        for zone in cfg.get("ocr", {}).get("zones", []):
            self._append_zone(zone)
```

In `collect_config` (`ui/workflow_designer.py`), find:

```python
        cfg["ocr"]["zones"] = [
            self.zone_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.zone_list.count())]
```

and change to:

```python
        cfg["ocr"]["mode"] = ("full_page"
                              if self.ocr_mode.currentText().startswith("Full-page")
                              else "zone")
        cfg["ocr"]["zones"] = [
            self.zone_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.zone_list.count())]
```

- [x] **Step 5: Wire the mode into `add_zone`/`edit_zone`**

Replace `add_zone` (line 518-524):

```python
    def add_zone(self) -> None:
        from ui.zone_dialog import ZoneDialog
        dlg = ZoneDialog(
            parent=self,
            full_page=self.ocr_mode.currentText().startswith("Full-page"))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            zone = dlg.build_zone()
            if zone:
                self._append_zone(zone)
```

Replace `edit_zone` (line 526-536):

```python
    def edit_zone(self) -> None:
        item = self.zone_list.currentItem()
        if item is None:
            return
        from ui.zone_dialog import ZoneDialog
        dlg = ZoneDialog(
            item.data(Qt.ItemDataRole.UserRole), parent=self,
            full_page=self.ocr_mode.currentText().startswith("Full-page"))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            zone = dlg.build_zone()
            if zone:
                item.setData(Qt.ItemDataRole.UserRole, zone)
                item.setText(self._zone_label(zone))
```

- [x] **Step 6: Update `_zone_label` to not show a meaningless `(0, 0) 0×0` for full-page zones**

Replace `_zone_label` (line 502-511):

```python
    @staticmethod
    def _zone_label(zone: dict) -> str:
        # a full-page-mode zone (or a template-seeded zone before its
        # first Edit…) has no captured pixel region — coordinates are
        # page-layout specific and don't transfer across projects.
        if "x" not in zone:
            return (f"{zone['name']} — full-page"
                    + (f" · /{zone['regex']}/" if zone.get("regex") else ""))
        return (
            f"{zone['name']} — "
            f"({zone.get('x', 0)}, {zone.get('y', 0)}) "
            f"{zone.get('w', 0)}×{zone.get('h', 0)}"
            + (f" · /{zone['regex']}/" if zone.get("regex") else ""))
```

- [x] **Step 7: Run the test to verify it passes**

Run: `pytest tests/test_phase2_ui.py -k ocr_mode_roundtrip -v`
Expected: PASS

- [x] **Step 8: Run the full UI test suite to confirm no regressions**

Run: `pytest tests/test_phase2_ui.py tests/test_index_tab.py tests/test_templates.py -v`
Expected: PASS

- [x] **Step 9: Commit**

```bash
git add ui/workflow_designer.py tests/test_phase2_ui.py
git commit -m "feat(ui): Workflow Designer extraction-mode toggle (zone vs full-page)"
```

---

### Task 5: Full regression pass

**Files:** none (verification only)

- [x] **Step 1: Run the complete test suite**

Run: `pytest -v`
Expected: PASS (aside from any pre-existing `skipif`s, e.g. tests requiring a tesseract binary not on this machine's PATH)

- [ ] **Step 2: Manually verify one full-page project in the running app**

Run: `python main.py`, open Workflow Designer, create/clone a project, switch "Extraction mode" to "Full-page (label-anchored regex)", add a zone with a label-anchored regex (e.g. `r"Invoice No:\s*(\S+)"`) and no region, load a sample page in the zone dialog, click "Test zone (run OCR)", confirm it shows the whole-page OCR read and the regex match — then Save the project and scan/import a batch through it, confirming the field appears in the index editor.

- [ ] **Step 3: Commit if any fixes were needed during manual verification**

```bash
git add -A
git commit -m "fix: address issues found during full-page OCR manual verification"
```

(Skip this step if no changes were needed.)
