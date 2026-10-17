# Making a release

A release is a tagged commit, built by script, with a checksum record. Nothing here pushes anywhere.

## Each release

1. **Decide the number** (MAJOR.MINOR.PATCH): a fix is PATCH, a new feature MINOR, a change that needs
   migration work on the customer's side MAJOR. Edit `version.py`.
2. **Write the changelog**: add a `## [x.y.z] - date` entry at the top of `CHANGELOG.md`, listing what
   changed for the user, grouped (Capture, Image quality, Indexing, Security, ...).
3. **Update the installer's number and check**: `python scripts\release.py --check`. It fails when
   `version.py`, `installer\setup.iss` and the newest `CHANGELOG.md` entry disagree. (A unit test and the
   CI "Lint and types" job run the same check.)
4. **Commit everything.** A release is built only from a clean tree.
5. **Build and verify**: `python scripts\release.py`
   - runs the whole test suite (including the slow real-OCR tests);
   - builds the exe (PyInstaller) and the installer (Inno Setup);
   - runs the exe's `--selftest`;
   - writes `dist\RELEASE.txt` with the version, the commit and the SHA-256 of both files.
6. **Install-test it**: `python scripts\install_test.py` (silent install on an empty profile, self-test,
   uninstall). Once, before the first customer installation, also install on a really clean Windows PC.
7. **Sign the installer** (and the exe) with the code-signing certificate, then re-run `install_test.py`
   and recompute the checksums. Until a certificate exists the installer is unsigned and Windows
   SmartScreen warns; say so in the hand-over.
8. **Tag it**: `python scripts\release.py --tag` (or `git tag -a vX.Y.Z -m "DOK Scanner X.Y.Z"`).
9. **Hand over** `DOKScanner_Setup.exe` with `RELEASE.txt` and the release notes
   (`docs\RELEASE_NOTES.md`). Keep the exact installer and the tag together.

## The central service

It is versioned with the application's commits. A change to its database needs a migration:
edit `central\models.py`, run `python -m central.manage new-migration "what changed"`, read the file it
writes in `central\migrations\versions\`, and commit it. `python -m central.manage db-status` and the
test `tests\central\test_migrations.py` fail when the models and the migrations disagree.
Update `docs\CENTRAL_API.md` with `python scripts\gen_api_doc.py` when a route changes (a test fails until
you do).

## Automatic checks

`.github/workflows/ci.yml` runs on every push and pull request: lint and types, the desktop tests on
Windows, and the central tests with a real PostgreSQL (including the migrations). In the repository
settings, require these checks before a change can be merged into `main`.

## Rolling back

Keep the previous installer. A newer installer over an older one upgrades the database after making a
copy (`dokscanner.db.before-vN.bak`); an older program refuses a newer database, so **to go back, install
the old program and restore the database copy** (or a backup).
