# Dashboard browser test

Drives the central admin dashboard in a real Chrome and saves screenshots.

    # 1. a throw-away central server (fresh database, one admin)
    set CENTRAL_DATABASE_URL=sqlite:///dash.db
    set CENTRAL_SESSION_SECRET=dashboard-e2e-secret-0123456789abcdef0123
    set CENTRAL_COOKIE_SECURE=0
    python -c "from central.db import make_engine, make_session_factory; from central.settings import Settings; from central.manage import create_admin; create_admin(make_session_factory(make_engine(Settings.from_env().database_url)), 'root', 'correct horse battery')"
    python -m uvicorn central.app:create_app_from_env --factory --port 8765

    # 2. the browser test
    npm i puppeteer-core
    set OUT=shots & set CHROME="C:/Program Files/Google/Chrome/Application/chrome.exe" & node e2e.js

To also test the alert webhook, start the server with `CENTRAL_DATA_KEY` (from `python -m central.manage generate-key`)
and `CENTRAL_WEBHOOK_ALLOW_HTTP=1`, and run the test with `WEBHOOK_PORT=9099`: it starts a small receiver, sets the
webhook up through the Settings page, sends a test message and checks the signature.

It signs in, adds a PC (enrollment code), renames it, creates and assigns a
project, adds an admin, reads the audit log, reloads (session kept) and signs
out, and fails on any browser console error or unexpected HTTP error.
