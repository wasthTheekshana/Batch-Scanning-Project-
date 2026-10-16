// The settings a central admin edits as a form instead of raw JSON: processing
// settings only (OCR, quality checks, separation, features and index fields).
// Scanner, export and delivery stay on each PC. Nothing here touches the page:
// it turns a config object into form values and back, so it can be tested.
(function (root) {
  "use strict";

  const STRATEGIES = [
    ["barcode", "Split on a barcode"],
    ["blank_page", "Split on a blank page"],
    ["zone", "Split on text in a region"],
    ["fixed_pages", "A fixed number of pages per document"],
    ["document_type", "Split at each form's title page"]];
  const BARCODE_ROLES = [
    ["first_page", "The barcode is on the document's first page"],
    ["cover_sheet", "The barcode is on a separate cover sheet"],
    ["auto", "Detect: a page that is only a barcode is a cover sheet"]];
  const OCR_MODES = [
    ["zone", "Regions (read fixed boxes of the page)"],
    ["full_page", "Text patterns (find each value by its label)"]];

  const isObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
  const num = (v, fallback) => (v === "" || v === null || v === undefined || Number.isNaN(Number(v)) ? fallback : Number(v));

  // config -> plain values the form shows
  function toForm(config) {
    const c = isObject(config) ? config : {};
    const sep = isObject(c.separation) ? c.separation : {};
    const ocr = isObject(c.ocr) ? c.ocr : {};
    const qc = isObject(c.qc) ? c.qc : {};
    const features = isObject(c.features) ? c.features : {};
    return {
      ocr_lang: ocr.lang || "eng",
      ocr_mode: ocr.mode || "zone",
      ocr_confidence: ocr.confidence_threshold === undefined ? 0.75 : ocr.confidence_threshold,
      qc_enabled: qc.enabled !== false,
      qc_pass_score: qc.pass_score === undefined ? 85 : qc.pass_score,
      qc_blur_warn: qc.blur_warn_threshold === undefined ? 80 : qc.blur_warn_threshold,
      qc_blur_fail: qc.blur_fail_threshold === undefined ? 30 : qc.blur_fail_threshold,
      qc_dpi_warn: qc.dpi_warn === undefined ? 200 : qc.dpi_warn,
      qc_dpi_fail: qc.dpi_fail === undefined ? 150 : qc.dpi_fail,
      strategies: Array.isArray(sep.strategy) ? sep.strategy.slice() : [],
      barcode_role: sep.barcode_role || "first_page",
      barcode_pattern: sep.barcode_pattern || "",
      drop_blank_pages: !!sep.drop_blank_pages,
      fixed_pages: sep.fixed_pages || "",
      auto_index: !!features.auto_index,
      duplicate_check: !!features.duplicate_check,
      fields: (Array.isArray(ocr.zones) ? ocr.zones : []).map((z, i) => ({
        i, name: isObject(z) && z.name ? String(z.name) : "", regex: isObject(z) && z.regex ? String(z.regex) : ""})),
      document_types: Array.isArray(ocr.document_types) ? ocr.document_types.length : 0,
    };
  }

  // What is wrong with the values, in words an admin can act on.
  function validate(f) {
    const problems = [];
    const inRange = (v, lo, hi) => Number.isFinite(v) && v >= lo && v <= hi;
    if (!inRange(num(f.ocr_confidence, NaN), 0, 1)) problems.push("OCR confidence must be between 0 and 1.");
    if (!inRange(num(f.qc_pass_score, NaN), 0, 100)) problems.push("The QC pass score must be between 0 and 100.");
    for (const [key, label] of [["qc_blur_warn", "blur warning"], ["qc_blur_fail", "blur failure"],
                                ["qc_dpi_warn", "DPI warning"], ["qc_dpi_fail", "DPI failure"]]) {
      if (!inRange(num(f[key], NaN), 0, 100000)) problems.push("The " + label + " level must be a number.");
    }
    if (f.strategies.includes("fixed_pages") && !(num(f.fixed_pages, 0) >= 1))
      problems.push("Enter how many pages each document has (1 or more).");
    const seen = new Set();
    for (const row of f.fields) {
      const name = String(row.name || "").trim();
      if (!name) { problems.push("Every index field needs a name."); continue; }
      if (seen.has(name.toLowerCase())) problems.push("The index field '" + name + "' appears twice.");
      seen.add(name.toLowerCase());
    }
    return problems;
  }

  // values -> a new config: only the form's keys change, everything else
  // (zones' boxes, document types, local settings) is kept as it was.
  function applyForm(config, f) {
    const out = JSON.parse(JSON.stringify(isObject(config) ? config : {}));
    out.ocr = isObject(out.ocr) ? out.ocr : {};
    out.qc = isObject(out.qc) ? out.qc : {};
    out.separation = isObject(out.separation) ? out.separation : {};
    out.features = isObject(out.features) ? out.features : {};
    out.ocr.lang = String(f.ocr_lang || "eng").trim() || "eng";
    out.ocr.mode = f.ocr_mode;
    out.ocr.confidence_threshold = num(f.ocr_confidence, 0.75);
    out.qc.enabled = !!f.qc_enabled;
    out.qc.pass_score = Math.round(num(f.qc_pass_score, 85));
    out.qc.blur_warn_threshold = num(f.qc_blur_warn, 80);
    out.qc.blur_fail_threshold = num(f.qc_blur_fail, 30);
    out.qc.dpi_warn = Math.round(num(f.qc_dpi_warn, 200));
    out.qc.dpi_fail = Math.round(num(f.qc_dpi_fail, 150));
    out.separation.strategy = STRATEGIES.map(([k]) => k).filter((k) => f.strategies.includes(k));
    out.separation.barcode_role = f.barcode_role;
    const pattern = String(f.barcode_pattern || "").trim();
    if (pattern) out.separation.barcode_pattern = pattern; else delete out.separation.barcode_pattern;
    if (f.drop_blank_pages) out.separation.drop_blank_pages = true; else delete out.separation.drop_blank_pages;
    const pages = Math.round(num(f.fixed_pages, 0));
    out.separation.fixed_pages = pages >= 1 ? pages : null;
    out.features.auto_index = !!f.auto_index;
    out.features.duplicate_check = !!f.duplicate_check;
    const original = Array.isArray(config && config.ocr && config.ocr.zones) ? config.ocr.zones : [];
    out.ocr.zones = f.fields
      .filter((row) => String(row.name || "").trim())
      .map((row) => {
        const zone = Object.assign({}, isObject(original[row.i]) ? original[row.i] : {});
        zone.name = String(row.name).trim();
        const regex = String(row.regex || "").trim();
        if (regex) zone.regex = regex; else delete zone.regex;
        return zone;
      });
    return out;
  }

  const api = { STRATEGIES, BARCODE_ROLES, OCR_MODES, toForm, validate, applyForm };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.DokForm = api;
})(typeof window !== "undefined" ? window : globalThis);
