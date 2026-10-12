# DOK Scanner — UAT & Go-Live Checklist (HLD §18 weeks 12–13, §14.3)

Run every item on a clean target machine (Windows 10 21H2+ / 11 x64).
Record pass/fail + notes per item; all must pass before go-live sign-off.

## 1. Installation
- [ ] `DOKScanner_Setup.exe` installs from USB in < 3 minutes
- [ ] Silent install works: `DOKScanner_Setup.exe /VERYSILENT /NORESTART`
- [ ] App starts with no Python/.NET pre-installed; login screen appears
- [ ] Default `admin`/`admin` logs in; password change works (Admin → Users)

## 2. Security
- [ ] `%LOCALAPPDATA%\DOKScanner\dokscanner.db` does NOT start with
      "SQLite format 3" (SQLCipher-encrypted; check with a hex viewer)
- [ ] Operator account: no Projects/Admin tabs, cannot review QC
- [ ] QC officer account: can review, cannot start scans
- [ ] Every action of this UAT session appears in audit_log and
      `logs\audit.log`

## 3. Capture → QC → export flow
- [ ] File import: multi-page PDF explodes to pages, thumbnails visible
- [ ] TWAIN or WIA scan from the site's scanner model works (duplex if
      supported); note exact scanner model: ______________
- [ ] Hot folder: file copied into the watch folder auto-starts a job
- [ ] Blank separator page splits documents; separator page dropped
- [ ] Barcode separator sheet splits and value lands in extracted_fields
- [ ] Blurred/skewed test pages: skew auto-corrected; blur page held in
      QC Review; accept/reject updates status + audit trail
- [ ] Exported searchable PDF opens and Ctrl+F finds body text

## 4. Delivery
- [ ] Local folder delivery: file present, checksum in delivery_detail
- [ ] SFTP (if in scope): document arrives on LAN SFTP server; size and
      MD5 recorded; unplugging network mid-batch → retry then flagged
- [ ] CD/DVD limit: Only the startup project's disc queue can be burned in the Admin tab; do not enable CD/DVD delivery on other projects yet.
      Confirm the Workflow Designer shows the same hint under the CD/DVD
      "Enabled" box.
- [ ] CD/DVD (if in scope): stage batch → Admin → CD/DVD → burn; disc
      checksum verify passes; burn_log.json entry written

## 5. Backup & disaster recovery (HLD §14.3 — REQUIRED before sign-off)
- [ ] Admin → Backup → Run backup now produces a `.dokbak` archive
- [ ] Export the master key (admin): note storage location: ____________
- [ ] On a CLEAN machine:
      `python scripts/restore.py <archive> <dir> --key-hex <key>`
      (or the packaged restore tool) — restore succeeds
- [ ] Restored DB opened in SQLite browser: all documents and index
      records intact; document count matches source: ____ / ____
- [ ] Result documented in the UAT report

## 6. Performance (HLD §19)
- [ ] `benchmark.py --pages 100` on the target PC ≥ 20 pages/min
      end-to-end (record: ______ pages/min)
- [ ] Operator reviews 20 flagged pages in ≤ 6 minutes (≈200/hour rate)
- [ ] UI stays responsive while a 100+ page batch processes

## 7. Resilience
- [ ] Kill the app mid-batch (Task Manager) → relaunch → job resumes
      and completes; no duplicate documents
- [ ] Power-loss simulation (hold power button) → DB passes integrity
      check on restart; latest backup restorable

## 8. Central sync (multi-PC)
- [ ] Admin > Central: register with a one-time code from the central admin;
      status shows connected
- [ ] The assigned project appears in the project list after the first sync
- [ ] Edit the project on central: the new version arrives on the next sync;
      the Designer shows a "Managed centrally" banner, only its scanner /
      export / delivery fields are editable (saving changes just those) and
      rollback of that managed project is refused
- [ ] Unassign the project on central: it stays on the PC, editing is enabled
      again
- [ ] Revoke the PC key on central: banner shows re-enrolment is needed and
      the PC stops polling; a new code restores sync
- [ ] Stop the central server, then start DOKScanner: it starts within a few
      seconds and scans normally with the last-known projects
- [ ] Admin > Central > Disconnect: the projects stay and become editable
- [ ] A project whose rule has a `..` set_folder value (publish via the
      central API) is skipped (listed as skipped), other projects still apply
- [ ] After enrollment (no restart), scans are delivered to the same place as
      before (output folder, SFTP/local delivery, scanner)
- [ ] After a sync that applied something, the Central tab says the updated
      settings apply to the next scan batch and the status bar shows no
      restart notice (also for an operator); scan again WITHOUT restarting and
      confirm the new central settings are used
- [ ] Per-project scanning: with two projects that have different export
      formats and delivery folders, pick the SECOND project in the scan wizard
      and confirm the document is exported in that project's format, delivered
      to that project's folder (the first project's folder stays empty) and
      filed under that project in the batch/document lists
- [ ] Edit a project (Workflow Designer, or on central) and scan again WITHOUT
      restarting: the new settings (e.g. export format) are used by the next
      batch; a batch already running keeps the settings it started with
- [ ] Refusing a broken batch (developer-assisted; needs the repo and its
      Python): close the app, then from the repo root run
      `python -c "import os; from pathlib import Path; from data.db import Database; from data.keystore import KeyStore; r = Path(os.environ['LOCALAPPDATA']) / 'DOKScanner'; db = Database(r / 'dokscanner.db', key=KeyStore(r / 'keys' / 'master.bin').get_or_create()); db.conn.execute(\"UPDATE projects SET config_json = '{oops' WHERE project_id = '<project_id>'\"); db.conn.commit(); db.close()"`
      (the database row is what the pipeline reads; the project's `.json` file
      is not, so editing the file does not break it). Start the app, choose that
      project in the scan wizard and start a scan: a "Project not available"
      message appears, it shows the project id and the reason only (no
      configuration values) and no batch or audit entry is created. Saving the
      project in the Workflow Designer or a central sync repairs it
- [ ] Interrupted batch of a broken project: start a scan, kill the app while
      it runs, break the project as above, restart: a "Batches not recovered"
      box lists the batch; the batch shows as failed
- [ ] Duplicate detection is cross-project: scan the same page into two
      projects (duplicate check on) and confirm the second document is held for
      review

## Sign-off
| Role | Name | Date | Signature |
|---|---|---|---|
| DOK Solutions | | | |
| Zerostix | | | |
