---
name: run-dokscanner
description: Run, launch, start, screenshot or smoke-test the DOKScanner desktop app (PyQt6, Windows) headless, and run the multi-PC central-sync end-to-end check against the real central server. Use when asked to run the app, take screenshots of its tabs, check the Central tab / Workflow Designer / enrolment flow, or confirm a change works in the real app (not just in pytest).
---

# Run DOKScanner (headless) and the central-sync check

DOKScanner is a PyQt6 desktop app (`main.py`). It is driven **headless**: Qt's
`offscreen` platform renders the real widgets, `widget.grab()` saves a PNG, and two
blocking calls in `main()` are patched (the modal login dialog and `QApplication.exec`).
The real `main()` still runs, including startup, central startup sync and clean
shutdown. No display, no xvfb: Windows + `QT_QPA_PLATFORM=offscreen` is enough.

Paths below are relative to the repo root. `PY` is the project venv's Python:
`.venv\Scripts\python.exe` (in a git worktree there is no venv: use the main checkout's,
e.g. `"D:/Project/Batch scanning & document capture/.venv/Scripts/python.exe"`).

## Prerequisites

Python 3.11 venv with the app's and the central service's dependencies:

```bash
PY -m pip install -r requirements.txt -r central/requirements.txt
```

Tesseract is optional: without it the app logs "OCR disabled" and still runs.

## Run (agent path) — the two drivers

Both live in `.claude/skills/run-dokscanner/driver/`, use a throw-away app-data dir
under `.superpowers/run-dokscanner/` (git-ignored; your real `%LOCALAPPDATA%\DOKScanner`
is never touched), sign in as the seeded `admin/admin`, and exit non-zero on failure.

**1. Launch the real app and screenshot every tab** (works on any branch):

```bash
PY .claude/skills/run-dokscanner/driver/screenshot_app.py
```

Prints one `[PASS]` line per tab and the visible status-bar text; PNGs land in
`.superpowers/run-dokscanner/shots/NN-<tab>.png`. A fresh, never-enrolled app shows
**no** central notices in the status bar (backward-compatibility check). Open the PNGs:
a blank frame means the launch failed.

**2. Full central-sync end-to-end** (needs the `central/` package; the client side lives
on the `central-sync` branch):

```bash
PY .claude/skills/run-dokscanner/driver/check_central.py
```

It starts the real central server (uvicorn on a free loopback port, throw-away SQLite DB,
fresh admin `root`), drives the admin API like the future dashboard would, then runs the
genuine app and clicks through: enrol with a one-time code, first sync, Workflow Designer
on a managed project (local delivery/SFTP editable, central-owned editors disabled, a
sneaked central-owned edit is NOT saved), a central edit arriving while the PC's local
settings survive, key revoked (banner + polling stops), re-enrol, disconnect. 41 checks;
screenshots in `.superpowers/run-dokscanner/shots/01-…06-*.png`. The server is stopped
in a `finally`; server log: `.superpowers/run-dokscanner/central/server.log`.

Both print a lot of timestamped `logging` lines first; filter them with
`| grep -v "^20[0-9][0-9]-"` to see only the check results.

## Run (human path)

`PY main.py` opens the real window and blocks in the Qt event loop; log in with
`admin` / `admin` on first run. Useless headless. To try central sync by hand, start the
server yourself (`CENTRAL_DATABASE_URL`, `CENTRAL_SESSION_SECRET` of 32+ chars,
`CENTRAL_COOKIE_SECURE=0` for plain http, then
`PY -m uvicorn central.app:create_app_from_env --factory --port 8765`), set
`DOKSCANNER_CENTRAL_ALLOW_LOOPBACK_HTTP=1` for the app (the client is https-only
otherwise), and use Admin → Central.

## Test

```bash
PY -m pytest -o addopts=""            # whole suite, ~1.5-3 min, prints the summary line
PY -m pytest tests/test_central_ui.py tests/test_central_sync.py -o addopts="" -q
```

Set `QT_QPA_PLATFORM=offscreen` for UI tests. `pytest.ini` has `addopts = -q`, so a plain
run prints only dots and no "N passed" line; `-o addopts=""` restores it.

## Gotchas

- **`QMessageBox` blocks forever offscreen.** Any scenario that triggers an information/
  warning/question box must monkeypatch it first (the drivers do:
  `QMessageBox.information = staticmethod(lambda *a, **k: None)`).
- **`findChildren(QLabel)` returns hidden labels too.** The main window's status bar always
  contains its restart/re-enrol notices, hidden until needed; filter with
  `not w.isHidden()` or you will "see" notices that are not shown.
- **The app only trusts `%LOCALAPPDATA%`.** `main.data_dir()` reads it, so the drivers point it
  at a sandbox; the central client keeps its API key as a DPAPI blob in
  `<appdata>/DOKScanner/keys/central_api_key.bin`, settings in `central.json`.
- **The central client is https-only.** Loopback `http://` works only with
  `DOKSCANNER_CENTRAL_ALLOW_LOOPBACK_HTTP=1` (set by `setup_env(..., allow_loopback_http=True)`).
- **Pulled changes apply to scanning after a restart** (the pipeline binds one project at
  start-up); the Designer/wizard see them from the database. The status bar and Central tab
  say so.
- **KNOWN GAP:** the Workflow Designer builds its project list once at start-up, so a project
  that arrives from a sync is not listed until `designer.refresh_projects_keep_selection()`
  is called (the driver calls it and prints a `[NOTE]`). Remove that workaround when fixed.
- Offscreen fonts look odd (a fallback pixel font) — normal; layout and state are what matter.
- The central server needs `CENTRAL_SESSION_SECRET` ≥ 32 characters or it refuses to start.
  `central.manage create-admin` prompts with `getpass`; the driver creates the admin
  non-interactively through `central.manage.create_admin` instead.

## Troubleshooting

- `StopIteration` while selecting a project in the Designer → the stale project list above;
  call `refresh_projects_keep_selection()` first.
- A screenshot that shows central notices on a never-enrolled app → you read hidden labels
  (see Gotchas), not a bug.
- `check_central.py` says "central server did not start" → read
  `.superpowers/run-dokscanner/central/server.log` (usually a missing dependency from
  `central/requirements.txt` or a too-short session secret).
