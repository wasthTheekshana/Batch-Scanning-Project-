# Changelog

All notable changes to DOK Scanner. The newest release is first. The version number lives in
`version.py` and must match the top entry here (a test checks it).

## [Unreleased]

### Structure (no change in behaviour)
- **The `core` package is split by purpose:** `pipeline/` (job queue, stages, hot folder, split/merge), `projects/` (settings,
  schema, templates), `security/` (users and roles), `search/`, `sync/` (central connection) and `shared/` (safe paths, data
  classes, crash guard); the event bus lives with the screens in `ui/`. Imports, patch targets and file paths in code,
  tests, build files and docs were updated; the central server's Docker image copies `projects/` instead of `core/`.
- **Two large classes are split into focused modules.** The database class (`data/db.py`, 822 lines) keeps the connection,
  schema and migrations and takes its queries from `projects_repo`, `documents_repo`, `custody_repo` and `users_repo`;
  the pipeline (`pipeline/job_queue.py`, 856 lines) takes the indexing and the last (export and delivery) stage from
  `indexing.py` and `delivery_stage.py`. The public methods and the logger names are unchanged.
- **The two big screens are split too.** The workflow designer (`ui/workflow_designer.py`, 1034 lines) builds its form
  group box by group box (`designer_form.py`) and takes the project selection, settings, zones and types, saving and
  rules from `designer_selection`, `designer_config`, `designer_zones`, `designer_save` and `designer_rules`; the main
  window (`ui/main_window.py`, 524 lines) takes its sidebar blocks, session handling and notices from
  `sidebar_blocks.py`, `main_session.py` and `main_notices.py`. Names tests and callers import are unchanged.
- **`scripts/check_layers.py`** (also a test and a CI step) fails when a package imports one it must not, so the layering
  described in the README cannot drift back.

### Review and Index tabs
- **Every page can be seen.** A document of several pages showed only its first page. Both tabs now have *Previous page* /
  *Next page* and "Page 2 of 4" under the image (hidden for a one-page document); the QC review pages the original and
  the cleaned-up image together.
- **They open on all documents.** The drop-down above the list starts on *All documents* (it used to start on the review
  queue / needs indexing), so the user sees everything and picks any document; the queue filters are one choice away.

### Export
- **A later batch no longer replaces an earlier batch's PDFs.** Files were named `{barcode}_{counter}`; every form of a
  bundle has the same barcode and the counter restarts at 1 in each batch, so the second batch overwrote the first's
  files (on a real run, 6 of 10 PDFs). A name already taken now gets the document's short id added. A document with no
  barcode is named `NO-BARCODE_0001.pdf` instead of `_0001.pdf`.

### Separation
- **No more forms glued together.** A QR code that the scanner's threshold had fattened (3 of 10 on a real batch) could
  not be read, and its form joined the one before. A second decoder (zxing-cpp) now reads two of three such codes, and
  the bank templates also start a new document at each form's title page, so all 10 forms of that batch split correctly.
  Existing projects keep their old setting: `python scriptseset_demo.py --index-only "Project" --no-wipe --yes` applies it
  (and the three-field index) to a project without deleting anything.

### Sign-in
- **Sign out no longer closes the program.** It returns to the sign-in page (the same one as at start-up), where the
  same or another user can sign in; background work (scanning, processing, delivery) carries on. Closing the window
  still quits, and so does *Cancel and sign out* on the sign-in page. The idle lock's Sign out does the same.
  Anything the user still had open (viewers, editing or review dialogs) is closed first, so nothing of the old session
  stays on screen behind the sign-in page.

### Indexing
- **Handwriting reader works in the application.** It was switched off whenever the ordinary OCR engine was
  present (which is always), so names and branches were read by Tesseract alone.
- **Better digit reading.** Boxed digits (account number, NIC) are read by a small convolutional network
  (`ocr/digit_cnn.onnx`) that votes with the older digit network and Tesseract: 97.5% of 119 handwritten digits
  from real forms right, up from 90%. A digit the readers disagree about marks the field for the operator.
  Pale pencil or light pen on grey box lines no longer loses the writing.
- **Branch kept as written.** A branch that is not on the list is no longer forced onto the nearest listed one:
  the reading of the ordinary engine and the handwriting model compete with the list, and the best fit wins.
- **Empty boxes read as empty.** A blank name box no longer produces an invented sentence.
- **Second pages.** A home or education loan's second page is no longer taken for a personal loan's.
- **Faster processing.** Boxed digits skip Tesseract when both digit networks agree (86% of boxes on real forms),
  a document's pages are read for the PDF text layer side by side, and pages are processed by up to 4 workers.
  Ten pages' fields: 139 s down to 85 s before the parallel stages.
- **`scripts/reset_demo.py`** starts a demo fresh: deletes documents, batches, jobs, custody events, the audit log
  and the output, delivered and staging files, keeps projects and users, and can make a project read only account
  number, NIC and branch (`--index-only`). A copy of the database is kept in `reset-backup\`.
- **New built-in template "Bank - Demo (account, NIC, branch)":** the same forms, reading only those three fields
  (fewer to confirm and faster). Existing projects keep their old settings: create a new project from the template.

Changes since 1.0.0. At release time this heading becomes the new version number and date.

### Central web center
- **Deleting documents.** An administrator can delete a document's index data and file details from the server
  (document page, *Delete this document...*, with a reason that goes to the audit log). Every revision and search entry
  is removed; a marker stays so the station does not send it again (it answers 410 and the PC drops it from its queue).
  The PDF is not held centrally and the station's own copy is not touched. Auditors and searchers cannot delete.
- **Running it for real.** A station reports how many documents it has not been able to send (a count and a time),
  and the server raises *backlog* (waiting 4 hours or more, critical after a day) and *refused* alerts; Fleet and the
  station page show the queue, and the station page lists the documents it sent. The Overview warns when the server has
  no data key, no longer fills with routine polls, and poll records older than 14 days are dropped. Search results
  download as a spreadsheet (at most 5,000 rows, formulas neutralised, logged). Documents a PC had refused for lack of a
  project are retried automatically once that project is assigned.
- **Index sync.** Each station reports every finished document's index fields and the PDF's details (name, size,
  SHA-256, where it went), never the PDF. A queue on the PC (saved in the same transaction as the document) is sent
  in the background, retried after an outage and never twice. Central stores the fields encrypted and finds documents
  by exact identifier (NIC, account number, any field) through a blind index; every revision is kept. New **Search**
  and document pages, a `searcher` role limited to chosen projects, and an access log (`GET /admin/documents`,
  `/admin/documents/{id}`, `/admin/documents/access-log`; stations use `PUT /pcs/{id}/documents/{doc}/meta|index`).
  Admin > Central on the PC shows what is waiting and offers *Send existing documents*. Needs `CENTRAL_DATA_KEY`.
- **Station page.** Click a station for its settings history against what it runs, 30 days of activity, its alerts
  and recent events (`GET /admin/fleet/{pc_id}`).
- **Webhook safety.** The address is re-checked at every send and the connection goes to the vetted address, so a
  name re-pointed at the server after saving cannot be used to reach it.
- **Fleet monitoring.** Each station reports, with every sync, its program version, the settings version
  of each project it runs, and today's counts of documents, pages, batches and errors (counts only, never
  document content). New **Fleet** and **Activity** screens, and an Overview that leads with the state of the
  fleet; the admin API has `GET /admin/fleet` and `GET /admin/stats`.
- **Alerts.** The Overview lists what needs attention (a station that stopped checking in, settings published but
  not applied, errors, revoked access, a station that never connected), most serious first and with how long, and the
  side menu shows a count; `GET /admin/alerts`.
- **Alerts by webhook.** Settings, Alert notifications: the server messages a chat tool or ticket system when an alert
  opens, becomes critical, stays critical for 12 hours, or is resolved (once each, retried on failure, optionally
  signed). The address is stored encrypted with a new server data key (`CENTRAL_DATA_KEY`, `manage generate-key`).
- The dashboard has a new side-menu layout that works on a phone (tables become cards) and in dark mode.
- Central migration 0002 adds the reporting tables; starting the service applies it.
- A database made by the old `create_all()` is now adopted at the schema it actually has.

## [1.0.0] - 2026-10-17

First release for the bank demo and pilot.

### Capture
- Scan from the Kodak S3060 through its TWAIN driver: both sides of every sheet, about one second
  per sheet (the WIA route loses the back of the last sheet and takes about four seconds per page).
  The scanner is chosen automatically when the same scanner offers both.
- Import PDFs and images, or watch a hot folder.
- Separate documents by barcode; discard empty pages; a barcode page is treated as a cover sheet
  or as content depending on the project.
- Custody bundles: QR label sheets (one or several copies), automatic stage tracking, expected
  document counts, reconciliation and a combined timeline.

### Image quality
- Enhancement no longer blurs scans: dust-only despeckle, contrast boost only for faded pages,
  no repeated filtering or resampling in QC, adaptive black-and-white from a grayscale scan.
- Large dark areas (section bars, logos) are no longer bleached, and round shapes in the margin
  are no longer painted over as punch holes.
- Each document type can have its own clean-up (same as the project, gentle, strong, none).

### Indexing and handwriting
- Handwriting reader (TrOCR, runs offline) for names, e-mail, mobile and branch, each corrected
  with what is known about the field; branch is chosen from the bank's branch list.
- New digit model trained on 70,000 handwritten digits; boxes are read by pen colour so strokes
  crossing printed lines survive; hidden box dividers are restored.
- Form definitions for the Personal Loan and Credit Card applications (alongside Education loan
  and Home loan), matched to the scan automatically.
- Index tab flags values the reader was unsure of; format checks for NIC, date, phone, e-mail.
- Search tab over documents and index fields, with CSV download.

### Review and operator workflow
- QC Review: accept, reject, flag for rescan, edit a page, and now **split or merge documents**
  when the automatic separation was wrong.
- Correct a page's document type; bookmarks and index are redelivered.
- Repair path for a damaged project file.

### Security
- Sign-in lockout (5 wrong passwords lock the account for 15 minutes; an administrator can unlock)
  and an idle lock that asks for the password again after 15 minutes without input.
- Forced password change from the default; minimum length; bcrypt; encrypted database (SQLCipher);
  master key protected with DPAPI.
- Audit log of sign-ins, failures, locks, unlocks, edits and deliveries.

### Central administration service
- FastAPI service with admin dashboard: projects and templates with versioning and credential
  scanning, PC enrolment with one-time codes and revocable keys, push/assign, audit, admin users
  and an auditor role.
- Database schema is now managed by migrations (Alembic); existing databases are adopted.
- Deployment package: Docker Compose with PostgreSQL and a TLS proxy, nginx and systemd option,
  nightly backup and restore.

### Interface
- New sidebar navigation (folds to icons on small windows), page title bars, consistent controls.
- Layouts rebuilt so every tab fits a small laptop; the window remembers where it was.

### Installer
- Inno Setup installer (unsigned until a code-signing certificate exists); silent install supported.
- The handwriting model (about 245 MB) is installed beside the application.

### Fixed
- Windows race in the running-index lock that made concurrent writers crash.
- Documents carrying a custody QR were dropped as label sheets; an all-excluded batch now says so.
- Scanner no longer left locked after an aborted TWAIN scan.

## [0.9.0-rc1] - 2026-10-04

Release candidate: the six-stage pipeline (ingest, OCR, enhance, QC, rules, delivery), per-project
configuration, rules engine, searchable PDF export, local/SFTP/CD-DVD delivery, workflow designer,
project templates, encrypted storage, and the first version of the central service.
