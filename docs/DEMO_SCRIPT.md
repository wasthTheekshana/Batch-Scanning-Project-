# Bank demo: script and checklist

For the presenter. About 15 minutes of demo, plus questions. Everything below
works today; the "Be honest about" boxes list what does not.

## The story

A branch receives a customer's papers: an application form, an ID card, a
letter. Today someone files them by hand and types the details into another
system. With DOK Scanner the branch scans one stack; each customer becomes
**one PDF**, named by a barcode, with the right details already filled in and
every step recorded.

## Before the day (T-1)

1. `python scripts/demo_pack.py` builds all sample files into `demo_kit/`.
2. `python scripts/demo_setup.py --reset` creates a clean **demo copy** of the
   app's data (`%USERPROFILE%\DOKScannerDemo`, separate from real data), with
   the login `admin` / `admin` and the **Bank - Customer file** project.
3. Rebuild the exe (`pyinstaller docscanner.spec`) and start the demo with
   `scripts\launch_demo.bat`. Check it opens and signs in.
4. **Rehearse with the scanner** (once, with the exact paper you will use):
   print `demo_kit/REHEARSAL_print_me.pdf` one-sided at 100% (7 sheets), load
   the feeder in that order, run `python scripts/rehearsal.py scan`. All checks
   must pass. Then run `demo_setup.py --reset` again so the demo starts clean.
5. Print `cover_sheets_CIF-100001-100020.pdf` (page 1 is customer
   CIF-100001) and keep `barcode_labels_...pdf` for the sticker example.
6. Charge the laptop, close other apps, note the scanner model (Kodak S2050,
   USB), keep a copy of `demo_kit/` on a USB stick.
7. Plan B if the scanner fails: import `demo_kit/bank_batch.pdf` through the
   same wizard ("File import") and say so.

## The demo

### 1. Sign in and the project (2 min)
- Sign in `admin` / `admin`. Tabs: Scan, QC Review, Index, Job Monitor,
  Reports, Projects, Admin, Custody.
- **Projects:** open *Bank - Customer file*. Show *Separation* ("a page with a
  barcode is: automatic", "discard blank pages") and *Document types*: NIC,
  Education loan application (a real bank form read by region), Application,
  Letter. Say: "A new document type is a pattern plus fields; no
  programming."

### 2. Print the barcodes (1 min)
- **Scan -> Print cover sheets.** Prefix `CIF-`, start 100001, how many 5, add
  to list, *Create PDF*. Show a cover sheet. Mention stickers (30 per sheet)
  for papers that cannot carry a cover sheet.

### 3. Scan a customer stack (4 min)
- Stack: cover sheet, ID card page, the bank's loan form (filled), a letter.
  One-sided pages are fine: the empty backs are discarded.
- **Scan -> New scan batch**, project *Bank - Customer file*, source
  *Scanner: KODAK S2050*, **Scan from document feeder**, watch the page count,
  Next, Finish.
- **Job Monitor** shows the stages. Result: **one** document.

### 4. The result (3 min)
- **Index tab:** the customer's fields (account number, name, date of birth,
  NIC, mobile, e-mail, address...) are filled. Change one value by hand and
  *Save*: operators can always correct.
- Open the exported PDF (`output` folder): named `CIF-200001_0001.pdf`, **no
  cover sheet, no blank backs**, with bookmarks NIC / Education loan application
  / Letter.
- Second stack (or the bank_batch.pdf file) with a *different* barcode: a
  second customer, a second PDF.

### 5. Control and evidence (3 min)
- **Custody tab:** create a bundle ("Box 12"), print its QR sheet, record
  checkpoints (receipt, scanning, QC, storage), show the reconciliation table
  and the timeline; **Export timeline (CSV/PDF)**.
- **Reports** tab: QC report and audit timeline export.
- **QC Review:** a poor scan is held for review, not delivered (use a skewed
  or blurred page if you have one).

### 6. Several branches (optional, 2 min)
- `python scripts/central_demo.py`, sign in `admin` / `demo-admin-passphrase`:
  register a PC, assign the project, show the audit log. "Settings are
  published once and each PC pulls them; no documents leave the PC."

## Security answers (all true today)
- The database is **encrypted** (SQLCipher); its key is protected by the
  Windows account (DPAPI). Backups are encrypted.
- Passwords are stored as **bcrypt** hashes; roles (admin, operator, QC
  officer, viewer) limit what each person sees and does.
- Every action is written to an **audit log**; the chain-of-custody timeline
  exports to CSV/PDF.
- The central service stores **configuration only, never documents or
  credentials**; PCs keep their own scanner, export and delivery settings.
- No cloud service is involved; delivery is to a local folder, SFTP, or disc.

## Be honest about
- **Handwriting.** The OCR engine reads printed and typed entries well; neat
  block capitals in the boxes are workable but not guaranteed. Anything it
  misses is corrected in the Index tab.
- **The form patterns** for NIC, application and letters are starting points;
  the education loan form is tuned on the bank's actual PDF (applicant side
  only; the joint-applicant column is not read yet). Real samples of each
  form are needed to tune the rest.
- **The installer is not code-signed** yet, so Windows may show a warning on a
  fresh PC. Demo from a machine where it is already installed.
- The central dashboard is a local demo; hosted deployment (HTTPS) is not set
  up.
- Other scanner models need testing; the Kodak S2050 is tested through WIA.

## If something goes wrong
| Problem | Do this |
|---|---|
| Scanner not listed | unplug and replug the USB cable; reopen *New scan batch*; else use File import |
| "feeder is empty" / jam message | reload the sheets squarely, close the cover, scan again |
| Fields wrong or empty | correct them in the Index tab (it is part of the story) |
| App will not start | run `scripts\launch_demo.bat` again; `demo_setup.py --reset` for clean data |
