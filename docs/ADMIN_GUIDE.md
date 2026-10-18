# DOK Scanner: administrator guide

For the person who looks after users, projects, backups and the connection to the central service.
Daily use is in `docs/OPERATOR_MANUAL.md`; installing in `docs/INSTALL_GUIDE.md`; the central server in
`central/README.md` and `central/deploy/DEPLOY.md`.

## 1. Where things are kept

Everything the program stores for a Windows user is under `%LOCALAPPDATA%\DOKScanner\`:

| Folder or file | What it holds |
|---|---|
| `dokscanner.db` | the database (documents, batches, users, custody, audit), encrypted with SQLCipher |
| `keys\master.bin` | the master key, protected with Windows DPAPI for this user |
| `projects\` | one settings file per project (all versions) |
| `staging\` | working images while a batch is processed |
| `output\`, `delivered\` | exported PDFs and index files, and their local delivery copies |
| `backups\` | encrypted backup archives (`.dokbak`) |
| `logs\audit.log`, `logs\crash.log` | the text audit trail; the record of any crash (type and code location only, never document text) |
| `selftest.txt` | the result of the last `DOKScanner.exe --selftest` |

Another Windows account on the same PC has its **own** data and its own master key. Documents and users
are not shared between accounts.

The program files (installed to `C:\Program Files\DOKScanner`) include `models\trocr\` (the handwriting
model) and, in a full release, `tesseract\`. Uninstalling removes them but leaves the data folder alone.

## 2. Users and roles (Admin > Users)

| Action | How |
|---|---|
| Add a user | *Add user...*: name, role, a first password (8+ characters, not the user name) |
| Change role | select the user, *Change role...* |
| Switch off / on | *Activate / deactivate* (a deactivated user cannot sign in; history is kept) |
| Reset a password | *Reset password...* (the user can then choose their own) |
| **Unlock** | *Unlock account*: after five wrong passwords an account is locked for 15 minutes; the table marks it `LOCKED`. |

Roles (what each may do):

| Role | Permissions |
|---|---|
| admin | all of them |
| operator | scan, view own batches, custody, search |
| qc_officer | QC review, view all batches, reports, custody, search |
| viewer | view all batches, reports and report export, search |

Security behaviour you should know: the first sign-in of the built-in `admin` account forces a new
password; **after 15 minutes without input a session locks** and needs the password again (set the
environment variable `DOK_IDLE_MINUTES` to another number of minutes, or `0` to turn it off); every
sign-in, failure, lock, unlock, password change and role change is written to the audit log.

## 3. Projects (Projects tab)

A project is a complete recipe: how pages are separated, cleaned, checked, read, exported and
delivered. Documents of different customers or kinds belong in different projects.

- **New from template...** starts from a built-in or saved template. For banking use *Bank - Customer
  file*: it contains the document types **Credit card application, Personal loan application, Education
  loan application, Home loan application, Application, NIC, Letter**.
- **Clone...**, **Version history...** (every *Save (new version)* keeps the old one; you can roll back),
  **Save as template...** to reuse a finished project.
- **An existing project keeps the settings it was created with.** Improving a template does not change
  projects made from it earlier: create a new project from the template, or add the new document types by
  hand.

### Document types and fields

Under *OCR / index fields*, each **document type** has: a pattern that recognises the page (usually its
title), optional patterns for the form's later pages, how its fields are read (**regions**: a box per field
on a sample form; or **text patterns**: find each value by its label), the **index fields**, and
**Image clean-up** for pages of this type:

| Setting | Effect |
|---|---|
| Same as the project | use the project's enhancement settings |
| Gentle | straighten and crop only: best for handwritten forms |
| Strong clean-up | all filters, for faded or noisy printed pages |
| None | keep the scan exactly as scanned |

*Learn from a blank form...* stores the printed words of a form so scans printed smaller or crooked are
still read in the right boxes; draw the field regions on the same blank form.

A field box can be a *line of text* or *one character per box*. **Reading handwriting from a field you
draw yourself uses the older, weaker method**: the handwriting reader is switched on, per field, in the
built-in bank forms. See section 4.

### Rules, export, delivery

Rules (if/then on document properties), the export format and file naming, the index file (CSV, XML or
JSON, delivered with each PDF), SFTP and CD/DVD delivery are all in the same tab. A project can switch
on a **hot folder**: files that a scanner or MFP drops into the folder become batches by themselves; each
file is then moved to `processed\` or `failed\` inside that folder.

## 4. Handwriting and the word lists

The handwriting reader (`models\trocr\` next to the program) reads names, e-mail, mobile and branch on
the bank forms. Without those files the program still works and falls back to the older reading;
`DOKScanner.exe --selftest` shows which is in use ("handwriting model: reads ..." means it is working).

The corrections use two lists you can improve **without a new release**:

| List | Default | Replace with the bank's own |
|---|---|---|
| Branch names | `ocr\branches.txt` (a general list of Sri Lankan towns) | set `DOK_BRANCHES` to a file with one branch per line |
| Common names | `ocr\names.txt` | set `DOK_NAMES` to your file |

**Important:** the branch reader chooses the closest branch of the list. A branch that is *not* in the
list is replaced by the nearest listed one (with low confidence, so the Index tab highlights it). Use the
bank's complete branch list.

## 5. Backup and restore (Admin > Backup)

Backups are encrypted archives written to a folder you choose, on a schedule (default every 24 hours)
and on demand (*Run backup now*). They contain the database and the project files.

1. **Export the master key (once, right after installing)** and keep it somewhere safe and separate: a
   backup cannot be opened on another PC without it. The file is only for administrators.
2. To restore on a PC:

       python scripts\restore.py <archive.dokbak> <output folder> [--key-hex <64 hex characters>]

   On the original PC the key loads automatically; on a clean PC pass the exported key.
3. Test a restore on a spare PC before you rely on the backups.

## 6. CD/DVD (Admin > CD/DVD)

Projects with CD/DVD delivery queue their documents for burning. The tab lists the queue of every such
project; burning needs the `burner_cmd` configured for the project (ImgBurn or cdrecord). Each disc is
checksum-verified and recorded in the disc log.

## 7. Connecting to the central service (Admin > Central)

Central lets an administrator publish projects once and have each PC pull them.

1. On the dashboard (or `POST /admin/pcs`) register the PC; you get a **one-time enrolment code** valid
   for 60 minutes.
2. On the PC: Admin > Central, enter the server address (**https**) and the code, *Register*. The
   PC's key is stored protected on that PC and never displayed.
3. Assigned projects are pulled in the background and apply to the **next** batch. Central owns the
   processing settings; the PC keeps its own scanner, export and delivery settings.
4. A lost or stolen PC: revoke its key in the dashboard (`DELETE /admin/pcs/{id}/key`), then issue a new
   code. A PC whose key was revoked shows *Central: new enrollment code needed* in the status bar.

### Watching the stations (web center: Overview, Fleet, Activity)

Sign in to the central dashboard in a browser (`https://your-server/dashboard`). Each station reports, with
every sync, its program version, which settings version of each assigned project it runs, and today's
counts of documents, pages, batches and errors. **Never any document content.**

| Screen | What it tells you |
|---|---|
| Overview | the state of the fleet in a sentence (how many stations are online, how many stopped checking in, how many are behind on settings), a status bar, and a short list of stations that need a look, with the reason |
| Fleet | one row per station: online, offline, access revoked or not enrolled; its program version (*older* when others run a newer one); **Settings** (*Up to date*, *Behind* with the version the station has and the one assigned, *Not reported*); today's counts. Stations that need attention are listed first; the list refreshes itself every 30 seconds |
| Activity | documents, pages, batches or errors per day for 7, 14, 30 or 90 days, and a table per station |

Reading it: *Behind* means you published a newer version of a project and the station has not applied it
yet (it applies at its next sync; offline stations catch up when they return). *Not reported* is a station
with an older program that does not send these details. An *error* is a document that was rejected or
whose delivery was reported unsuccessful. A read-only *auditor* can see all three screens.

The central server's own users, roles (including the read-only *auditor*), lockout and audit log are described
in `central/README.md`; its API is in `docs/CENTRAL_API.md`.

## 8. Health checks and troubleshooting

| Check | How |
|---|---|
| Is the installation sound? | `DOKScanner.exe --selftest` (writes `selftest.txt`); every line should start with `ok` |
| What happened? | `logs\audit.log` (who did what) and `logs\crash.log` (a crash) |
| Is the central server's HTTPS right? | `python scripts\check_tls.py https://your-server` |
| Disk space | `%TEMP%\_MEI*` folders are leftovers of crashed runs: `python scripts\clean_temp.py` removes stale ones |
| A project will not open | at start-up the program lists unreadable projects and an administrator can choose **Repair...**, which offers to restore each from its last good version |

Data protection: the database holds customer identity numbers and account numbers. There is no
automatic deletion yet; decide a retention period with the bank and remove documents accordingly. Keep
the data folder, the backups and the exported master key on access-controlled storage.
