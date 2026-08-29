# Full-Page OCR Auto-Indexing — Design

**Status:** Approved for implementation
**Source:** Follow-up from Enhancement Plan §4.1 "OCR Engine Accuracy" discussion — reframed by user direction from an accuracy-benchmarking exercise into a layout-adaptiveness feature: each project's documents can vary in layout per-document, so a fixed per-field pixel zone (drawn once, on one sample) doesn't reliably locate the field across all of a project's real documents. This adds an alternative extraction strategy that OCRs the whole page and locates each field by a label-anchored regex instead of a fixed rectangle.

## Problem

Auto-indexing today (`ocr/zone_extractor.py`, wired into `core/job_queue.py::_extract_zone_fields`) is purely zone-based: an admin drags a rectangle on one sample page per field (`ui/zone_dialog.py`), and that fixed `x/y/w/h` (scaled by `ref_w`/`ref_h` for resolution differences) is cropped out of every subsequent page and OCR'd. This works well when a project's documents share one fixed layout, but breaks down when field position varies from document to document within the same project (e.g. invoices from different vendors) — the crop lands on the wrong part of the page, or nothing at all.

## Goal

Let an admin choose, per project, an extraction mode that OCRs the whole page and finds each field's value anywhere in the page text via a label-anchored regex (e.g. `Invoice\s*No\.?\s*:?\s*(\S+)`), so indexing survives layout drift without needing per-document zone redrawing.

## Non-goals

- Not a replacement for zone-based extraction — both modes coexist per project; existing projects/templates default to `"zone"` and need no changes.
- Not a keyword/label auto-detection system (no guessing at field locations without a regex) — the admin still writes one regex per field, same as today; only where it's matched against changes (whole page vs. one crop).
- Not a second OCR pass — full-page mode reuses the whole-page OCR result (`record.ocr_text` / `record.ocr_confidence`) that `core/job_queue.py::_ocr_worker` already computes for every page, so it's one OCR call per page either way (fewer than zone mode's 1 + N crop calls).
- Not per-word confidence scoring — `OCRResult` (`ocr/tesseract_engine.py`) only carries a page-level mean confidence, and the injectable `ocr_fn` contract (used by tests and production alike) doesn't expose word-level data. Full-page-mode fields use the page's aggregate confidence; this is an honest, coarser number than zone mode's per-crop confidence, not a regression to fix here.

## Data model / config

New optional key `ocr.mode` in project config (`core/project_schema.json`, `core/profile_manager.py::DEFAULT_PROJECT`): `"zone"` (default) or `"full_page"`. Absent = `"zone"`, so every existing saved project and all 5 built-in templates keep their current behavior unchanged.

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
}
```

In `"full_page"` mode, each zone entry only needs `name` and `regex` — `x/y/w/h/ref_w/ref_h` are ignored if present (so a project can be switched between modes without discarding previously-drawn zone geometry).

## Components

**`ocr/zone_extractor.py`** — new function alongside the existing `extract_fields()`:

```python
def extract_fields_from_text(text: str, confidence: float,
                             zones: list[dict]) -> dict[str, FieldValue]:
    """Full-page mode: locate each field's value in already-OCR'd page
    text via its label-anchored regex, instead of cropping a fixed zone.
    A zone with no regex is skipped (unanchored, page-wide matching has
    no way to know which part of the page belongs to it)."""
```

For each zone with a `regex`: `re.search(pattern, text)`, same "first group or whole match" rule as `extract_fields()`. No match → `FieldValue("", 0.0)`. Match → `FieldValue(value, confidence)` using the page-level confidence passed in. A zone with no `regex` is skipped with a `log.warning` (not silently dropped, not raising — consistent with `_extract_zone_fields`'s existing try/except-and-warn pattern around extraction failures).

**`core/job_queue.py::_extract_zone_fields`** — branches on `ocr_cfg.get("mode", "zone")`:

```python
if mode == "full_page":
    from ocr.zone_extractor import extract_fields_from_text
    fields = extract_fields_from_text(record.ocr_text or "",
                                      record.ocr_confidence or 0.0, zones)
else:
    from ocr.zone_extractor import extract_fields
    fields = extract_fields(image, zones, lang=ocr_cfg.get("lang", "eng"),
                            ocr_fn=self.ocr_fn)
```

`record.ocr_text`/`record.ocr_confidence` are already populated by `_ocr_worker` (job_queue.py:235) before `_extract_zone_fields` is called (job_queue.py:239) — full-page mode reads them rather than re-invoking `ocr_fn`. Everything downstream of `fields` (threshold flagging, lookup-table validation) is unchanged — both modes produce the same `dict[str, FieldValue]` shape.

**`ui/workflow_designer.py`** — one new control: an "Extraction mode" combo box (Zone-based / Full-page) next to the zone list, bound to `cfg["ocr"]["mode"]`. Passed through to `add_zone()`/`edit_zone()` so `ZoneDialog` knows which mode it's editing for. `_zone_label()` in full-page mode shows `name — /regex/` without the `(x, y) w×h` position (meaningless in this mode).

**`ui/zone_dialog.py`** — `ZoneDialog(zone=None, parent=None, full_page=False)`:
- `full_page=True` hides/disables the drag-region controls (coords row) — position isn't collected.
- `build_zone()` doesn't require `w`/`h` > 0 when `full_page=True`; it returns `{"name": ..., "regex": ...}` only (no `x/y/w/h/ref_*`).
- `test_zone()` runs OCR on the *whole* loaded sample (not a crop) when `full_page=True`, then applies the regex to that full text — same `_show_test_result()` rendering either way, so the admin still sees "OCR read → after regex → stored value" feedback before saving.
- `suggest_regex()` is disabled (grayed out with a tooltip) in full-page mode — its "Label : value" heuristic assumes a tight crop around exactly one field, which doesn't hold against a whole page of text.

## Error handling

Same shape as today's zone-mode error handling in `_extract_zone_fields`: any exception from `extract_fields_from_text` is caught, logged as a warning with the page number, and extraction is skipped for that page (existing behavior, unchanged — this is not new to this feature). A zone with no regex in full-page mode is a warning, not an exception — the rest of that page's zones still extract normally.

## Testing

- **`tests/test_zone_extractor.py`** (or extend existing OCR tests) — unit tests for `extract_fields_from_text()`: match found via label anchor, no match → empty/zero-confidence, zone with no regex → skipped (not present in output or present as empty — pick one and assert it), multiple zones matching different parts of the same text.
- **`tests/test_auto_index.py`** — new pipeline-level test mirroring `test_zone_fields_populate_extracted_fields`, using `mode: "full_page"` and a `fake_ocr` that returns a full page of text with an embedded `"Invoice No: INV-7777"` label, asserting the field lands in `extracted_fields` the same way, and that low-confidence flagging still works off the page-level confidence.
- Existing zone-mode tests are unaffected (mode defaults to `"zone"`; no changes to their fixtures needed).

## Open questions

None — this is additive and backward-compatible; no unresolved decisions block implementation.
