"""Start a local central admin service for a demo, with sample data.

    python scripts/central_demo.py            # http://127.0.0.1:8000/dashboard
    python scripts/central_demo.py --no-browser

Sign in as  admin / demo-admin-passphrase. Data lives in demo_kit/central_demo.db
(delete the file for a fresh start). Local only: plain HTTP on 127.0.0.1.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
DB = REPO / "demo_kit" / "central_demo.db"
PORT = int(os.environ.get("PORT", "8000"))
USER, PASSWORD = "admin", "demo-admin-passphrase"


def main() -> int:
    DB.parent.mkdir(exist_ok=True)
    os.environ["CENTRAL_DATABASE_URL"] = f"sqlite:///{DB}"
    os.environ.setdefault("CENTRAL_SESSION_SECRET", "central-demo-secret-0123456789abcdef-demo")
    os.environ["CENTRAL_COOKIE_SECURE"] = "0"          # plain HTTP, local only

    import uvicorn
    from sqlalchemy import select

    from central.app import create_app_from_env
    from central.db import make_engine, make_session_factory
    from central.manage import create_admin
    from central.models import AdminUser
    from central.settings import Settings

    factory = make_session_factory(make_engine(Settings.from_env().database_url))
    app = create_app_from_env()
    with factory() as db:
        have_admin = db.scalar(select(AdminUser).where(AdminUser.username == USER))
    if not have_admin:
        create_admin(factory, USER, PASSWORD)

    config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{PORT}"
    for _ in range(50):
        try:
            urllib.request.urlopen(url + "/healthz", timeout=1)
            break
        except OSError:
            time.sleep(0.2)
    print(f"Central admin: {url}/dashboard   (sign in: {USER} / {PASSWORD})")
    print("Press Ctrl+C to stop.")
    if "--no-browser" not in sys.argv:
        webbrowser.open(url + "/dashboard")
    try:
        while thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        server.should_exit = True
    return 0


if __name__ == "__main__":
    sys.exit(main())
