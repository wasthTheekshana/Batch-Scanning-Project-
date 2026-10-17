# DOK Scanner 1.0.0: release notes

Release for the bank demonstration and pilot. The detailed list of changes is in `CHANGELOG.md`.

## What it does

Scans or imports batches of bank documents, separates them into documents, cleans and quality-checks
every page, reads the index fields (including handwriting), lets staff correct what needs correcting,
delivers a PDF with its index file, and keeps a chain-of-custody record of the physical boxes. An
optional central service lets an administrator manage projects for many PCs from one place.

## New in this release

- **Both sides, fast:** the Kodak S3060 scans through its TWAIN driver, both sides of every sheet in
  about a second per sheet.
- **Clearer images:** enhancement no longer blurs text or bleaches dark areas; each document type can have
  its own clean-up (gentle for handwritten forms).
- **Better handwriting reading:** a new handwriting reader for names, e-mail, mobile and branch, a much
  better digit reader, and form definitions for the Personal Loan and Credit Card applications.
- **Fixing separation mistakes:** split a document or merge two, right in QC Review.
- **Safer sign-in:** lock after five wrong passwords (administrator can unlock), and a screen lock after
  15 minutes without input.
- **New look:** side menu, tidier layouts that fit a small laptop screen.
- **Central service:** managed database migrations, a tested HTTPS set-up, an API reference.
- **Documentation:** operator manual, administrator guide, installation guide, API reference.

## Known limitations (please read)

- **Handwriting is read well, not perfectly.** On the two filled sample forms tested, 6 of the 11 key fields
  were exactly right; branch, mobile number and NIC were the strongest, names and e-mail addresses the
  weakest. That is a very small test. Staff must check highlighted fields; plan a larger accuracy test on the
  bank's own forms before relying on automatic values.
- **Branch and name lists are placeholders.** The branch reader picks the closest branch of its list: a
  branch that is not listed is replaced by a wrong one (the field is highlighted for checking). Load the
  bank's real branch list before the pilot.
- **Only some forms are known.** Credit Card, Personal Loan, Education loan and Home loan applications have
  field positions defined. Other forms need their field regions drawn (Projects tab) or sent for setup, and
  fields drawn by hand do not yet use the handwriting reader.
- **The installer is not code-signed.** Windows SmartScreen will warn until a certificate exists.
- **No automatic deletion of old data.** Agree a retention period for identity and account numbers
  with the bank.
- **Existing projects keep their old settings.** A project made earlier does not receive new document types;
  create a new project from the template (or add the types by hand).
- **The central service's HTTPS was tested with a local certificate authority**, not with the bank's real
  domain. Run `scripts\check_tls.py` against the real address after deployment.
- **Not covered by this release:** multi-site consolidation, a mobile client, Sinhala and Tamil text
  recognition.

## Upgrading

Run the new installer over the old one. Data and settings are kept; the database is upgraded
automatically at the next start (a copy is kept first). See `docs/INSTALL_GUIDE.md`.
