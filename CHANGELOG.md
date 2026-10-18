# Changelog

All notable changes to DOK Scanner. The newest release is first. The version number lives in
`version.py` and must match the top entry here (a test checks it).

## [Unreleased]

Changes since 1.0.0. At release time this heading becomes the new version number and date.

### Central web center
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
