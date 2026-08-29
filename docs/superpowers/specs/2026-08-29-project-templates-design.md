# Project Templates — Design

**Status:** Approved for implementation
**Source:** `docs/DOK_Scanner_Enhancement_Plan (1).docx` §4.4 "Metadata Field Templates for Banking Documents" — generalized per user direction from a single banking-only preset into a reusable template system spanning multiple document/project kinds, with both built-in starter templates and admin-saved templates from real projects.

## Problem

Configuring a new project today means building every index zone, regex, and QC/enhancement setting from scratch in the Workflow Designer — there is currently no "start from a preset" path, and in fact no "create a brand-new project" path at all (the only project-creation entry point in the UI today is **Clone…**, which requires an existing project to clone from — confirmed by inspecting `ui/workflow_designer.py`, where `clone_project()` is the sole caller into `ProfileManager`, and `ProfileManager.create()` has no UI caller anywhere in `ui/*.py`). Every new client/document type currently means either hand-building a project from nothing or cloning an unrelated existing one and stripping it down.

## Goal

Let an admin start a new project from a reusable template — either one of a handful of built-in starter templates (Banking, Insurance, HR/Personnel, Legal/Contracts, General invoices) or one saved earlier from any of their own configured projects — so onboarding a new document type is fast, and the library of templates grows from real usage over time rather than staying fixed to whatever ships in the code.

## Non-goals

- Not a marketplace or sharing mechanism across separate DOK Scanner installations — templates live in this installation's own database, same as projects.
- Not a redesign of the project config schema (`core/project_schema.json`) — a template is a subset of that same schema, reused as-is.
- Not an automatic "detect document type and suggest a template" feature — the admin explicitly picks a template when creating a project.

## Data model

New table, `project_templates`, added to `data/db.py`'s `_SCHEMA` alongside the existing `projects`/`batches`/`documents` tables:

```sql
CREATE TABLE IF NOT EXISTS project_templates (
    template_id  TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    category     TEXT NOT NULL,
    config_json  TEXT NOT NULL,
    built_in     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);
```

`config_json` holds a **partial** project config — the reusable skeleton only: `ocr` (zones + confidence threshold + lang), `separation` (strategy, blank_threshold — not `zones` if those are page-image-relative to a specific sample, see Open Question below), `qc` (stages, thresholds), `enhancement_profile`, `rules`. It deliberately excludes everything specific to a live, running project: `project_id`, `name`, `version`, `created`, `export.output_path`, `delivery` (local/SFTP/CD-DVD paths and credentials), `cloned_from`. This mirrors exactly what `DEFAULT_PROJECT` in `core/profile_manager.py` already treats as configuration vs. what a live project additionally carries.

`category` is a free-text label (not an enum) — `"Banking"`, `"Insurance"`, `"HR/Personnel"`, `"Legal/Contracts"`, `"Invoices"` for the built-ins, and whatever an admin types when saving their own — grouping templates in the picker UI without hard-coding the list of allowed categories.

`built_in` distinguishes shipped templates (re-seeded/updated on app startup if the built-in JSON files change) from admin-saved ones (never touched by the seeding step).

## Built-in templates

Five JSON files under `core/templates/` (placed inside the `core` package, not a top-level directory — this matters for packaging: it reuses the exact bundling mechanism already proven for `core/project_schema.json`, see Packaging below), each a `config_json`-shaped partial config with a starter set of index zones (name + regex only, no x/y/w/h — coordinates are inherently sample-specific, so built-ins ship with zone *names and regexes* populated but position deferred to "click and place" the first time the admin loads a sample, which `core/project_schema.json`'s `"zones": {"type": "array"}` already permits with no `items` sub-schema forcing coordinate fields) plus sensible QC/enhancement defaults:

- `core/templates/banking.json` — Document Type, Customer/Account Reference, Branch Code, Document Date, Reference Number
- `core/templates/insurance.json` — Claim Number, Policy Number, Claimant Name, Date of Loss, Adjuster Reference
- `core/templates/hr.json` — Employee ID, Employee Name, Department, Document Type, Effective Date
- `core/templates/legal.json` — Contract Number, Party Names, Effective Date, Contract Type, Reference/Matter Number
- `core/templates/invoices.json` — Invoice Number, Vendor Name, Invoice Date, Amount, PO Reference

On app startup, any `core/templates/*.json` file whose `name` doesn't already exist as a `built_in=1` row is inserted (and if it exists with different content, its `config_json` is updated in place — built-ins are meant to improve as the shipped defaults improve; an admin who wants to customize one uses "Save as template" to fork it into their own admin-saved copy first, which is never touched by reseeding).

**Packaging:** `docscanner.spec`'s `datas` list needs `("core/templates", "core/templates")` added, mirroring the existing `("core/project_schema.json", "core")` entry. Unlike that entry, this one won't be caught by the existing `tests/test_packaging_spec.py` regression test — that test's regex only matches a single literal `Path(__file__).parent / "file.ext"` call, not a directory of files loaded by `glob()`. This is a known, accepted gap in that test's coverage for this one case; the packaging task in the implementation plan adds the `datas` entry directly rather than extending the regex (extending it to handle glob-based directory loading is out of scope for this change).

## Components

- **`core/template_manager.py`** (new) — the domain layer, mirroring `core/profile_manager.py`'s shape:
  - `TemplateManager(db)` — no filesystem component needed (unlike `ProfileManager`, templates don't need versioning/archive/rollback, so no `templates_dir`)
  - `list_templates() -> list[dict]` — all templates, built-in and admin-saved
  - `get(template_id) -> dict`
  - `save_as_template(project_config: dict, name: str, category: str) -> dict` — strips the live-project-only fields listed above, inserts a new `built_in=0` row
  - `seed_built_ins(templates_dir: Path | None = None) -> None` — called once at app startup (`main.py`), reads `core/templates/*.json` (default resolved via `Path(__file__).parent / "templates"`), upserts each as `built_in=1`
  - `build_project_config(template_id: str) -> dict` — **required because `core/project_schema.json` marks `export.output_path` and the full `delivery` block as required keys** (confirmed: `"required": ["format", "filename_template", "output_path"]` and `"required": ["local_folder", "sftp", "cd_dvd"]`), so a template's deliberately-partial config (no `export`/`delivery` at all) cannot be handed straight to `ProfileManager.create()` — schema validation would reject the missing keys. This method deep-merges the stored template config onto a fresh copy of `DEFAULT_PROJECT` (from `core/profile_manager.py`), so the result always has every schema-required key (with `DEFAULT_PROJECT`'s empty-string/default values for anything the template doesn't specify) while the template's zones/rules/QC/enhancement settings override those defaults. The Workflow Designer's "New from template…" flow calls this, then passes the result to `ProfileManager.create(name, config=result)` as normal.
- **`data/db.py`** — CRUD: `insert_template`, `get_template`, `list_templates`, following the exact style of the existing `insert_bundle`/`get_bundle`/`list_bundles` methods added for chain-of-custody.
- **`ui/workflow_designer.py`** — two additions to the existing top toolbar (next to the existing Clone…/Version history…/Save buttons):
  - **"New from template…"** — opens a small picker dialog (grouped by category, built-ins and admin-saved together), prompts for the new project's name, then calls `ProfileManager.create(name, config=template_config)` (the existing `create()` already accepts an optional starting `config` dict — confirmed in `core/profile_manager.py`) and refreshes the project list, selecting the new project.
  - **"Save as template…"** — enabled whenever a project is loaded; prompts for a template name + category, calls `TemplateManager.save_as_template(self.config, name, category)`.

## Data flow

1. **Startup:** `main.py` calls `TemplateManager(db).seed_built_ins()` (default path) once, after the DB is opened — same place `KeyStore.get_or_create()` and `open_database()` already run.
2. **New project from template:** admin picks a template in the new dialog → `TemplateManager.build_project_config(template_id)` merges it onto `DEFAULT_PROJECT` → `ProfileManager.create(name, config=merged)` → a real, versioned, live project exists exactly as if hand-built, just pre-filled. The admin then repositions the inherited zones against their own sample pages (existing zone-editor workflow, unchanged).
3. **Save as template:** admin has a working project open in the Workflow Designer → clicks "Save as template…" → `TemplateManager.save_as_template(config, name, category)` strips live-only fields → new `project_templates` row, immediately available in the picker for the next new project.

## Error handling

- `save_as_template` with a duplicate `name` for an existing template: allowed (templates aren't required to have unique names, same as projects aren't) — the picker disambiguates by showing category + name together, and a template row's own id is what's actually referenced.
- `seed_built_ins` reading a malformed `templates/*.json`: log a warning and skip that file rather than failing app startup — a broken built-in template file must never block the app from launching.
- `ProfileManager.create()` with a template's config: the existing `validate()` call in `create()` already guards against a malformed config reaching a live project — no new validation path needed.

## Testing

- `tests/test_templates.py` (new): `TemplateManager` CRUD, `save_as_template` correctly strips live-only fields (assert `delivery`/`export.output_path`/`project_id` absent from the stored `config_json`), `seed_built_ins` inserts on first run and updates in place on a changed built-in without touching an admin-saved template of the same name.
- Extend `tests/test_phase2_ui.py` (confirmed: this is where `WorkflowDesigner` is tested today — it already has a `designer` fixture and patterns like `test_designer_rollback_restores` for "click a designer action, verify DB state" — note `clone_project` itself has no existing test to mirror, so the new tests are establishing the pattern for both clone-shaped actions) with qtbot tests for the "New from template…" and "Save as template…" buttons.

## Open question, resolved for this pass

Should a built-in template's zones ship with real pixel coordinates (impossible without a matching sample page) or as name+regex only, positioned on first use? **Resolved: name+regex only**, positioned on first use — matches how the existing zone editor already works (an admin always loads a sample page and drags a region; there's no "coordinate-only" zone entry path today, and inventing one just for templates would be new surface area for no real benefit, since the admin has to look at their own scanned samples anyway to know where a field actually sits on their documents).
