# DOK Scanner: installation guide

## 1. What you need

| | |
|---|---|
| Computer | Windows 10 or 11, 64-bit. Memory: the running application used about 1.8 GB in testing, so 8 GB of RAM is a sensible minimum. Disk: the program with its handwriting model is about 0.5 GB; scanned pages need more, depending on volume and settings (measure on the pilot) |
| Rights | administrator rights to install (the installer is per machine) |
| Scanner | optional. Kodak S3060: install the scanner's own TWAIN driver; the scanner must then appear as *KODAK Scanner: S2000f/S3000* (TWAIN). WIA also works but loses the back of the last sheet |
| Network | not needed for scanning. Needed only for SFTP delivery and the central service |
| Antivirus | the installer is **not code-signed yet**; Windows SmartScreen may warn. Sign it before distributing outside the pilot (see `docs/RELEASE_PROCESS.md`) |

## 2. Installing

1. Copy `DOKScanner_Setup.exe` to the PC (about 345 MB: it includes the handwriting model).
2. Run it as administrator and follow the wizard (default folder `C:\Program Files\DOKScanner`).
3. Start **DOK Scanner** from the Start menu. First sign-in: user `admin`, password `admin`; the
   program makes you set a new password immediately.

**Silent / group-policy install** (no questions, no restart):

    DOKScanner_Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="C:\Program Files\DOKScanner"
    "C:\Program Files\DOKScanner\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART     (uninstall)

## 3. After installing

1. **Check the installation:** open a command prompt in the install folder and run

       DOKScanner.exe --selftest

   Every line must start with `ok`: the version, the modules, the barcode and OCR engines, the digit
   model, the **handwriting model** (`reads 'Jaffna'` or similar; if it says *model files not installed*
   the `models\trocr` folder is missing and handwriting is read the weaker way), the scanner drivers,
   SFTP, searchable PDF and password hashing. The result is also saved in `%LOCALAPPDATA%\DOKScanner\selftest.txt`.
2. **Sign in as admin and create the real users** (Admin > Users), then **export the master key** and
   store it safely (Admin > Backup). Without it a backup cannot be restored on another PC.
3. **Create the project** for the documents (Projects > New from template... > *Bank - Customer file*)
   and set its scanner, export folder and delivery.
4. **Connect the scanner:** switch it on, start a batch (Scan > New scan batch). The wizard lists the
   scanners it finds; the TWAIN entry is chosen automatically.
5. **Replace the word lists** `ocr\branches.txt` and `ocr\names.txt` with the bank's own (or point
   `DOK_BRANCHES` / `DOK_NAMES` at them): see `docs/ADMIN_GUIDE.md`.

## 4. Where the data goes

Per Windows user, under `%LOCALAPPDATA%\DOKScanner\`: the database, projects, exports, backups and logs
(listed in `docs/ADMIN_GUIDE.md`). **Uninstalling does not delete it**; **upgrading keeps it**. Install an
upgrade by running the new installer over the old one. The database is upgraded automatically at the next
start, after a copy is kept as `dokscanner.db.before-vN.bak`; a database written by a *newer* release is
refused rather than damaged.

## 5. Testing an installer before it goes out

On a development or build machine, `python scripts\install_test.py` does a silent install into a
temporary folder with an **empty user profile**, runs the self-test, checks that the program files and the
handwriting model are in place, then uninstalls and checks that the data folder was left alone. It is the
closest to a clean PC that can be done on a machine that already has the program; run the real thing on a
genuinely clean Windows PC once before the first customer installation.

## 6. Deploying the central service

The central service runs on a server, not on the scanning PCs. Docker Compose with PostgreSQL and an
HTTPS proxy is the recommended way: `central/deploy/DEPLOY.md`. After deploying, check it from
outside with `python scripts\check_tls.py https://your-server`; the database schema is brought up to date
by the service when it starts (`python -m central.manage db-status` shows the version).

## 7. Removing

Use *Apps > DOK Scanner > Uninstall*, or the silent command above. To remove the data as well, delete
`%LOCALAPPDATA%\DOKScanner` for each user **only after** exporting anything that must be kept (it holds
the customer documents' records and the encryption key).
