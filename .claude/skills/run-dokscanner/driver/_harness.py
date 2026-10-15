"""Shared helpers for driving the REAL DOKScanner main() headless.

DOKScanner is a PyQt6 desktop app. On Windows (and CI) we run it with
QT_QPA_PLATFORM=offscreen: real widgets, no visible window, and
`widget.grab()` renders them to a PNG. Two blocking calls in main() are
patched: the modal login dialog (auto-signs in as the seeded admin/admin) and
QApplication.exec() (runs our scenario instead of the event loop).
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]          # <repo>/.claude/skills/run-dokscanner/driver/
DEFAULT_OUT = REPO / ".superpowers" / "run-dokscanner"   # git-ignored


def setup_env(sandbox: Path, allow_loopback_http: bool = False) -> Path:
    """Fresh, throw-away app data dir; never touches the real %LOCALAPPDATA%."""
    appdata = sandbox / "appdata"
    shutil.rmtree(appdata, ignore_errors=True)
    appdata.mkdir(parents=True)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["LOCALAPPDATA"] = str(appdata)          # main.data_dir() reads this
    if allow_loopback_http:                            # central client is https-only otherwise
        os.environ["DOKSCANNER_CENTRAL_ALLOW_LOOPBACK_HTTP"] = "1"
    os.chdir(REPO)
    sys.path.insert(0, str(REPO))
    return appdata / "DOKScanner"


def run_main(scenario) -> int:
    """Run the genuine main(); `scenario(app, win)` replaces the Qt event loop."""
    from PyQt6.QtWidgets import QApplication, QDialog

    import main as dok_main
    from ui.login_dialog import LoginDialog
    from ui.main_window import MainWindow

    def fake_login_exec(self):
        self.username.setText("admin")
        self.password.setText("admin")
        self.try_login()
        return QDialog.DialogCode.Accepted

    def fake_app_exec(self=None, *_a):
        app = QApplication.instance()
        win = next(w for w in app.topLevelWidgets() if isinstance(w, MainWindow))
        win.resize(1280, 820)
        return scenario(app, win)

    LoginDialog.exec = fake_login_exec
    # The sandbox admin keeps the default password; skip the forced-change
    # prompt (it is a modal dialog and would block the headless run).
    dok_main.require_password_change = lambda *_a, **_k: True
    QApplication.exec = fake_app_exec
    return dok_main.main()


def pump(app, secs: float = 0.2) -> None:
    end = time.time() + secs
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def wait_for(app, predicate, timeout: float = 20.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def shot(app, widget, path: Path) -> bool:
    """Render `widget` to a PNG; returns False if the image looks blank."""
    pump(app, 0.3)
    path.parent.mkdir(parents=True, exist_ok=True)
    img = widget.grab()
    img.save(str(path))
    qimg = img.toImage()
    colours = {qimg.pixel(x, y)
               for x in range(0, qimg.width(), 37)
               for y in range(0, qimg.height(), 37)}
    return len(colours) > 3


class Report:
    def __init__(self):
        self.rows: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.rows.append((name, bool(ok), detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}".rstrip(), flush=True)
        return bool(ok)

    def note(self, msg: str) -> None:
        print(f"  [NOTE] {msg}", flush=True)

    def summary(self) -> int:
        bad = [n for n, ok, _ in self.rows if not ok]
        print(f"\n{len(self.rows) - len(bad)}/{len(self.rows)} checks passed"
              + (f"; FAILED: {bad}" if bad else ""), flush=True)
        return 1 if bad else 0
