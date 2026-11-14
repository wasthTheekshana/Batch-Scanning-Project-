# DOK Scanner roadmap: what is built, what is missing

Last reviewed 2026-10-16 (evening). This replaces the "what is left" part of
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
- Added 2026-10-15/16: Search tab with CSV download; index files (CSV/XML/JSON);
  hot folder; split by form title; form setup from a blank copy; Education and
  Home loan forms incl. joint column; field checks (NIC, date, phone, email) and
  low-confidence highlighting; custody bundles that track their documents and
  advance to QC; Kodak S3060 full-width scan; page orientation by Tesseract's
  orientation test; OCR benchmark script.
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

### 3b. Found while testing on the real Kodak S3060 (2026-10-16)
- Handwritten boxed digits on the Home loan form: account number 7/10 and NIC
  8/12 digits right; name, email, address and date of birth mostly missed. Needs a
  stronger hand-digit model and filled forms with the true values (`--real`).
- Home loan form: pages 2-4 and the Sinhala-language page 1 (same boxes, but no
  Sinhala OCR data to line it up by its labels) are not read.
- Duplex on the S3060 (found on the real scanner, 2026-10-16): two-sided WIA scanning
  DOES give both sides for every sheet EXCEPT THE LAST one in the feeder (after its
  front the driver answers "paper empty"; asking again at once, after a pause,
  re-arming, re-sending the setting or reconnecting did not help). The driver also
  drops blank backs by itself, so image counts cannot reveal a lost back. The Scan
  screen now says so (put a blank sheet after the last real sheet; the project's
  "discard empty pages" removes it). A proper fix needs the Kodak TWAIN driver
  (installed: `C:\Windows	wain_64\Kodak`, reachable from Python with pytwain):
  its scan call timed out twice here, probably waiting for paper it could not see,
  and each abort left the scanner locked until it was switched off and on, so it
  is NOT done. Tools: `scripts/scanner_probe.py`, `duplex_experiment.py`,
  `duplex_last_sheet.py`, `twain_probe.py`.
- Custody (DONE 2026-10-16): earlier documents can be linked to their bundles
  (button in the Custody tab), a bundle can carry an expected document count
  (mismatch flagged in the reconciliation). Storage and return stay manual,
  physical steps by design.

### 4. Handwriting and language
- Free handwriting is unreliable; boxed handwritten digits are about 82%.
  `scripts/ocr_benchmark.py` now measures it (report: `docs/OCR_BENCHMARK.md`):
  typed forms under five scan conditions read 94% exactly right (clean 300 dpi
  100%). Weak spots left: date of birth on black-and-white scans (0%, the
  boxes break), address on a noisy scan (38%). Typed text is an upper bound;
  run `--real DIR` on real scans with their true values to get the honest
  figure (needs a few filled forms with the answers written down).
- OCR is English only; Sinhala and Tamil language data are not bundled
  (ID cards and many forms carry both).

### 5. Operator workflow
- DONE 2026-10-16: correct a page's document type in the Index tab (the
  exported PDF's bookmarks are rewritten in place and the PDF and its index file
  are redelivered); a "Fields to check (low confidence)" queue; a repair for a
  project whose settings are damaged (restores the current file or the newest
  intact saved version, or the default settings after an explicit yes; the damaged
  text is kept aside).
- Field validation (NIC, dates, phone, email), unknown custody QR warning and
  the startup notice for unreadable projects were done earlier.
- Still open: lookup against a customer list (needs the bank's list).

### 6. Release readiness
- Code-signing certificate; install and silent-deploy test on a clean PC.
- Operator manual, admin guide, installation guide.
- Real-scanner rehearsal (`scripts/rehearsal.py scan`) and client UAT.
- ~~The hot-folder watcher is not connected~~ DONE 2026-10-15 (`pipeline/hot_folder.py`;
  per-project folder in the Designer's Scanner group; handled files move to
  processed/ or failed/; batches run as operator "hot-folder", so only roles
  that see all batches can search them). Untested with a real network scanner.

### 7. Central service
- DONE 2026-10-16: read-only `auditor` role; project settings edited as a form
  (OCR, quality checks, splitting, features, index fields) with the raw JSON one
  tab away; deployment package in `central/deploy/` (Docker Compose with
  PostgreSQL and a TLS proxy, or systemd + nginx, a backup script and restore
  steps, all checked by tests); a race fixed where two admins could disable
  each other.
- Verified 2026-10-16 with Docker: the concurrency tests pass on a real
  PostgreSQL 16 (15 runs, no failure); the image builds (context 140 kB with the
  new `Dockerfile.dockerignore`); the compose stack starts healthy, an admin and
  an auditor can be created, sign-in works, `backup.sh` writes a dump and a
  restore into a fresh database brings both accounts and the audit log back.
  Not tried here: the TLS proxy on ports 80/443 and a certificate for a public
  name (needs the real domain).
- STARTED 2026-10-18 (owner decision: a fuller dashboard): fleet monitoring. Stations report their program
  version, applied project versions and daily counts; new Fleet and Activity screens, an Overview led by the
  fleet's state, a phone layout, and alerts (quiet station, settings not applied, errors) on the Overview and as
  a count in the menu, and delivered by webhook, a per-station page (2026-10-18). Index sync built (design phases 1
  and 2, 2026-10-18; owner chose index data, no PDF files): stations report each document's index fields and PDF
  details through a queue that survives outages, central keeps them encrypted and searches by exact identifier, with
  a searcher role scoped to projects and an access log. Not built: corrections made centrally and sent back to the
  stations (phase 3), keeping the PDF bytes (paused), retention, key rotation tool, a backlog alert.

### 8. Needs a business decision, not code
Bank document-system field mapping (plan 3.5), incident and insurance workflow
(3.6), multi-site consolidation (3.3, 3.4, 3.8, 3.9), API integration (3.7),
security evidence (4.7, 7). Scale to 15M pages (4.2) is a real redesign.
`docs/SDLC.md` has known factual errors and is parked.

### 9. Housekeeping
- DONE 2026-10-16: the test run is 1.5 min quick / 2.5 min full in parallel
  (`scripts/run_tests.py`, was 8 min); the audit log no longer fills with one
  custody entry per page; the CD/DVD tab lists and burns every project's queue; a
  usability pass on Custody and Index (no clipped headings, buttons that need a
  selection are off until there is one).
- Still open: a fuller usability pass with real operators.

## After the missing items: improvement pass
Performance (large batches), usability of every tab, OCR accuracy tuning on the
bank's own documents, and a security review of everything added since the last
SAST run.
