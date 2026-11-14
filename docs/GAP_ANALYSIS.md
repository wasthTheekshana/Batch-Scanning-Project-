# DOK Scanner: Tech-lead gap analysis

Date: 2026-10-15. Sources: `docs/SRS_DOKScanner_General.docx` (v1.0, 24 Aug 2026), `DOK_HLD_v2.docx`,
`docs/ROADMAP.md`, `docs/ENHANCEMENT_PLAN_STATUS.md`, `docs/security/*`, and the code at commit `51c5f8e`.
Test suite at the time of review: full run to 100%, no failures, 1 skip.

Summary: the code is in good shape. What is missing is mostly engineering process and documents; the
paper trail has fallen behind the code.

Status column: OPEN, IN PROGRESS, DONE, DECIDED (decision taken, work not started).

## A. Requirements and documents

| # | Gap | Evidence | Status |
|---|---|---|---|
| A1 | The SRS is out of date: dated 24 Aug, "Draft", customer still `[Customer Name]`, no sign-off. | Missing from it: central service, multi-PC sync, chain of custody, search tab, index export (CSV/XML/JSON), document types and form recognition, field validation, project templates, hot folder as built, dashboard. | DONE: `docs/SRS_DOKScanner_v2.docx` (draft, awaiting customer sign-off) |
| A2 | SRS and HLD contradict the product: they say "no server infrastructure", "no network calls ever", "air-gapped". | `central/` is a FastAPI + Postgres server; PCs call it over HTTPS. | DONE in SRS v2.0 (needs a customer name and sign-off) |
| A3 | No traceability from requirement to design to code to test. | FR-1..FR-23 never appear in `tests/`. UAT has 44 items, none ticked. | OPEN |
| A4 | The SRS has no testable acceptance criteria. | e.g. "OCR 1,000-3,000 pages/hour". | DONE in SRS v2.0 |
| A5 | Missing documents: SDD, DB design, API spec, test plan, operator manual, admin guide, install guide, release notes. | `central/` has a threat model but no OpenAPI or API document. | PARTLY DONE 2026-10-17: operator manual, administrator guide, installation guide, central API reference (generated), release process and release notes written (`docs/`). Still to write: SDD, database design, test plan. |
| A6 | `CONTEXT.md` and `docs/adr/` do not exist although `CLAUDE.md` says they should. | Decisions (SQLCipher, central owns config but PCs keep delivery, per-project pipeline) live only in specs and memory. | PARTLY DONE: `docs/adr/` started with ADR 0001. `CONTEXT.md` and ADRs for the earlier decisions still to write. |
| A7 | `docs/SDLC.md` is wrong and overlaps `docs/security/SDLC_POLICY.md`. | Names HSQLDB, Credential Manager and `ui/auto_index`, none of which are in the code. The policy says "300 tests"; there are about 1,100. | OPEN |
| A8 | Documents contradict each other. | README says the dashboard is not started, the roadmap says it is built, `central/README.md` says the admin API is missing. SAST report covers 7,101 lines; code is about 18,000. `SECURITY_TESTING_SCOPE.md` says no network component exists. | DONE 2026-10-17 for the README and `central/README.md` (rewritten to match the code). `docs/SDLC.md` (A7) and the SAST report's coverage numbers are still to reconcile. |

## B. Requirements the code does not meet

| # | Requirement | Reality | Status |
|---|---|---|---|
| B1 | FR-12 PDF/A | `pdf_a` is only a flag; `export/batch_exporter.py` sends it through the plain reportlab writer. | DECIDED: searchable PDF is enough, PDF/A not supported. DONE 2026-10-15: the searchable PDF itself was only half built (text dumped at the top of the page, cut at 1,000 characters per line, no word positions); it now has a text layer on each word read from the final page image. `pdf_a` removed from the Designer; the schema still accepts it so old projects load. |
| B2 | NFR-6: 5-10 stations per server, 20+ users | One local database per PC; central syncs configuration only. | DESIGN WRITTEN 2026-10-15, awaiting approval: `docs/superpowers/specs/2026-10-15-central-document-sync-design.md` and `docs/adr/0001-central-document-and-index-sync.md`. Owner answers (2026-10-15): first release syncs index data and PDF details only, PDF storage paused; central copy is the secure archive of record, PCs keep working offline (store and forward), 10+ PCs, volume unknown. Build is phased (spec section 10) and needs gap C6 (migrations) first. |
| B3 | HLD: manual split/merge of documents in the viewer | Not built. | DONE 2026-10-17: QC Review > Split / merge... (`pipeline/doc_edit.py`, `ui/split_merge_dialog.py`). Results go back to the review queue; originals are kept as superseded. |
| B4 | Per-document-type enhancement profile | Profile is per project only. | DONE 2026-10-17: a document type can set its own clean-up (same as project / gentle / strong / none); the bank handwriting forms default to gentle. |
| B5 | FR-17 Viewer role | Defined, but only 8 RBAC tests; the Viewer path is not shown to be covered. | OPEN |
| B6 | Roadmap leftovers | Index tab per project, CD/DVD burn per project, Sinhala/Tamil OCR, read-only auditor role in central, Designer repair path for a corrupt project file. | PARTLY DONE: CD/DVD burn lists every project's queue (2026-10-16); read-only auditor role in central done; repair path for a damaged project file done (2026-10-16); Index tab uses each document's own project zones. Still open: Sinhala/Tamil OCR. |

## C. Engineering practice

| # | Gap | Why it matters | Status |
|---|---|---|---|
| C1 | The git repo has no remote (211 commits, one branch, no tags). | Source exists only on one PC. | OPEN: needs a remote repository URL from the owner (GitHub, GitLab or Azure DevOps, private). Then `git remote add origin <url>`, `git push -u origin main`, tag releases. |
| C2 | No CI, no lint or type-check configuration. | The SDLC policy promises a green run before merge but nothing enforces it. A full run takes minutes. | DONE 2026-10-17: `.github/workflows/ci.yml` (lint + types, desktop tests on Windows, central tests with PostgreSQL and migrations); ruff and mypy in `pyproject.toml`. It starts running when the repository is pushed to GitHub; then require the checks on `main`. |
| C3 | `requirements.txt` incomplete and unpinned. | `paramiko` and `pytwain` are imported but not listed; not named as hidden imports in `docscanner.spec`. Only `>=` ranges; runtime and test dependencies mixed. | DONE 2026-10-15: runtime deps pinned in `requirements.txt` (paramiko added; pytwain documented as optional); test and build tools in `requirements-dev.txt`. Not yet verified by a clean-machine install or a rebuilt exe. |
| C4 | Default login `admin/admin`. | No forced password change, no password policy, no lockout or idle timeout in the desktop app (central has them). | DONE 2026-10-17: five wrong passwords lock an account for 15 minutes (administrator can unlock), sessions lock after 15 minutes idle, all audited (`tests/test_login_protection.py`). Earlier: minimum 8 characters, default password refused, forced change. |
| C5 | No release management. | Version only in `installer/setup.iss`; no changelog, tags, signed build, or clean-PC install test. | PARTLY DONE 2026-10-17: `version.py` is the single version source, `CHANGELOG.md`, `scripts/release.py` (check, build, checksums, tag), `scripts/install_test.py` (silent install on an empty profile). Still open: a code-signing certificate and one install on a genuinely clean PC. |
| C6 | No schema migrations. | `data/db.py` said none are ever needed; will not hold after go-live. Central uses `create_all()` and needs Alembic. | DONE: PC side 2026-10-15 (`PRAGMA user_version`, ordered migrations, backup first, rollback, refuses a newer database). Central 2026-10-17: Alembic baseline 0001, existing `create_all()` databases adopted, `manage migrate / db-status / new-migration`, verified on a real PostgreSQL (`tests/central/test_migrations.py`). |
| C7 | No data retention or privacy policy. | NICs and account numbers are stored; nothing says how long or how they are purged (Sri Lanka PDPA). | OPEN |
| C8 | Maintainability. | `ui/workflow_designer.py` 1,141 lines, `pipeline/job_queue.py` 871; 50 broad `except Exception`; slow tests (10-13 s each in `test_loan_form.py`); a known flake. | PARTLY DONE 2026-10-17: the six silent `except: pass` now log and the linter forbids new ones (the 54 other broad catches are at hardware/OCR/delivery boundaries and log); `job_queue.py` 1,030 -> 845 lines and `workflow_designer.py` 1,159 -> 1,034 (the rest is `WorkflowDesigner.__init__`, 330 lines, still to split); the index-lock race that made a test flaky is fixed. Slow `test_loan_form.py` tests remain. |
| C9 | Housekeeping. | Untracked files in the repo root; release DLLs and `tesseract.exe` sit in the project folder (gitignored); `.superpowers/` holds run data. | PARTLY DONE 2026-10-17: stale `_MEI*` temp folders removed (6.6 GB) and `scripts/clean_temp.py` added; root PDFs are git-ignored. `docs/SDLC.md` and `docs/education-loan-english.pdf` are still untracked on purpose. |

## D. B2 design questions (documents and index sync) - answered, see the design spec

This changes the product from "one app per PC plus central config" to a shared system. Questions to settle
before any build; the answers go into SRS v2.0 and an ADR:

1. What is the central copy for: backup, search across all PCs, or the system of record?
2. Does a PC keep working when the server is unreachable (store and forward)? Recommended: yes.
3. What is synced: finished PDFs, index records, the audit log, custody records, or all of them?
4. Storage on the server: file share or object store for PDFs, Postgres for index data?
5. Volume: pages per day per PC and number of PCs (decides bandwidth and storage).
6. Conflicts: a correction made on the PC and on the server at the same time.
7. Security: bank documents then leave the PC, so TLS, per-PC keys, encryption at rest on the server and
   access control for search all become requirements.
8. Search tab: local only, or across the whole fleet?

## E. UI problems reported (2026-10-15)

Some windows are the wrong size and some buttons do not appear. To be reproduced with the
`run-dokscanner` screenshot driver, listed per window, and fixed with tests where practical. Status: first pass DONE 2026-10-15. The main window and every dialog now fit the screen (`ui/theme.py: fit_to_screen`); the Scan and Projects toolbars wrap; the Zone, Cover sheet and Document type dialogs no longer force a 1000-1400 px width (`tests/test_window_sizes.py`). The user should check the real PC and report any window still wrong, with the window name and screen resolution/scaling.

## Order of work

1. This round: this file, SRS v2.0, C1, C3, C4, UI sizing pass.
2. Next: B2 design and ADR, then traceability matrix and test plan (A3), CONTEXT.md and ADRs (A6), fix or
   delete `docs/SDLC.md` (A7), CI (C2).
3. Before go-live: C5, C6, C7, operator and admin guides, signing, UAT.
4. Features if the bank needs them: Sinhala/Tamil OCR, per-project Index and burn tabs, manual split/merge.
