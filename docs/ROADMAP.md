# DOK Scanner roadmap: what is built, what is missing

Last reviewed 2026-10-15. This replaces the "what is left" part of
`docs/ENHANCEMENT_PLAN_STATUS.md` (which stops at 2026-10-09). Work items that are
being built have specs and tickets under `.scratch/<feature>/` (see
`docs/agents/issue-tracker.md`).

## Built

- Capture: file import, Kodak S2050 through WIA (document feeder, duplex),
  barcode cover sheets and sticker labels (printed from the Scan tab), pages
  fed sideways or upside down are turned upright.
- Separation: barcode (cover sheet / first page / automatic), blank pages,
  fixed pages, zone triggers; empty pages discarded without splitting (audited).
- Indexing: per-project zones, document types per project (NIC, loan form,
  application, letter), regions or text patterns per type, boxed-character and
  handwritten-digit reading, forms lined up with their template automatically.
- Quality and control: QC pipeline with review queue, chain of custody
  (bundles, QR sheets, timeline, CSV/PDF export), audit log, RBAC, encrypted
  database and backups.
- Delivery: local folder, SFTP, CD/DVD burn queue; per-customer PDF with a
  bookmark for each document type.
- Multi-PC: central service (API, admin users, sessions, audit, PC enrolment),
  admin dashboard, sync client with per-project settings.
- Operations: installer (unsigned), crash guard with `crash.log`, `--selftest`,
  separate demo data (`scripts/demo_setup.py`), demo script and sample files.

## Missing, in priority order

### 1. ~~Index data never leaves the application~~  DONE 2026-10-15 (`.scratch/index-export/`)
Built: a CSV, XML or JSON index file is written next to each PDF and delivered
with it (optional running index.csv); an operator's correction in the Index tab
rewrites and redelivers it. Settings are in the Workflow Designer's export group
and switched on in the Bank - Customer file template.

### 2. Search and retrieval screen  (DONE: Search tab, `.scratch/document-search/`)
Nobody can find a stored document by customer ID, NIC, account number or date
and open its PDF.

### 3. Forms and documents need real-world coverage
- ~~Self-service form setup~~ DONE 2026-10-15: "Learn from a blank form" in the
  document-type dialog learns the anchor words (`ocr/sample.py`). Still to do:
  draw all zones in one step on that same sample (zones are drawn one by one).
- The Personal Loan form (different layout), the NIC and the letter patterns:
  need real blank/filled samples from the bank.
- The Joint Applicant column of page 1 is now read (`joint_*` fields, DONE
  2026-10-15, verified on synthetic fills only). Pages 2-4 (finances, credit
  card application) are still not read: they need a decision on which fields
  the bank wants indexed and a filled sample to check handwriting.
- ~~A stack without barcodes cannot be split by form type~~ DONE 2026-10-15: tick
  "A page like this starts a new document" on a document type and enable the
  `document_type` separation strategy.

### 4. Handwriting and language
- Free handwriting is unreliable; boxed handwritten digits are about 82%.
  Run the OCR engine benchmark (enhancement plan section 4.1) on real samples.
- OCR is English only; Sinhala and Tamil language data are not bundled
  (ID cards and many forms carry both).

### 5. Operator workflow
- Correct a page's document type (NOT DONE: page types are stored per page
  and drive the PDF bookmarks, so a correction after export needs the PDF
  re-exported, not just the index file); clearer review queue with
  low-confidence fields highlighted.
- ~~Field validation~~ DONE 2026-10-15 for NIC, dates, phone and email
  (`core/validation.py`; the Index editor marks a wrong value and asks before
  saving it). Lookup against a customer list still needs the bank's list.
- ~~Warning for an unknown custody QR~~ DONE (the page is left out of the
  documents, audited `custody_label_unknown`, one warning per batch).
  ~~Startup notice for unreadable projects~~ DONE. Designer repair path when a
  project file is also corrupt: not done.

### 6. Release readiness
- Code-signing certificate; install and silent-deploy test on a clean PC.
- Operator manual, admin guide, installation guide.
- Real-scanner rehearsal (`scripts/rehearsal.py scan`) and client UAT.
- The hot-folder watcher exists as a module but is not connected.

### 7. Central service
Read-only auditor role; form editor instead of raw JSON; deployment package
(HTTPS, Docker or service files, backups); Postgres concurrency tests; decide
whether the slim dashboard is enough or a fuller one is needed.

### 8. Needs a business decision, not code
Bank document-system field mapping (plan 3.5), incident and insurance workflow
(3.6), multi-site consolidation (3.3, 3.4, 3.8, 3.9), API integration (3.7),
security evidence (4.7, 7). Scale to 15M pages (4.2) is a real redesign.
`docs/SDLC.md` has known factual errors and is parked.

### 9. Housekeeping
Full test run takes 2-4 minutes with an occasional timing flake; audit-log
noise; CD/DVD burning only works for the startup project; the Index tab
and admin screens need a usability pass after the above.

## After the missing items: improvement pass
Performance (large batches), usability of every tab, OCR accuracy tuning on the
bank's own documents, and a security review of everything added since the last
SAST run.
