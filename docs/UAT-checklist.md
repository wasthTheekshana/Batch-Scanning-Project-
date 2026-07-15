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

## Sign-off
| Role | Name | Date | Signature |
|---|---|---|---|
| DOK Solutions | | | |
| Zerostix | | | |
