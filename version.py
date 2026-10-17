"""The one place the application's version number is written.

`scripts/release.py` copies it into the installer script and checks it against CHANGELOG.md;
`tests/test_release_files.py` fails when the three disagree. Change it only when preparing a
release (see docs/RELEASE_PROCESS.md)."""
__version__ = "1.0.0"
