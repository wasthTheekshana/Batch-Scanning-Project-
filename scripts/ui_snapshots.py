"""Render every tab of the main window to PNGs at given window sizes (offscreen), so
layout problems (squashed tables, clipped text) can be looked at.

    python scripts/ui_snapshots.py DATA_COPY_DIR OUT_DIR [WxH ...]

DATA_COPY_DIR holds a COPY of the application data (dokscanner.db, projects/).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtWidgets import QApplication

from core.event_bus import EventBus
from core.profile_manager import ProfileManager
from core.rbac import Session, User
from data.db import Database
from data.keystore import KeyStore
from ui.main_window import MainWindow
from ui.theme import apply_theme


def main(argv) -> int:
    data, out = Path(argv[1]), Path(argv[2])
    sizes = [tuple(int(v) for v in s.split("x")) for s in argv[3:]] or [(1280, 720)]
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    apply_theme(app)
    db = Database(data / "dokscanner.db", key=KeyStore(data / "keys" / "master.bin").get_or_create())
    pm = ProfileManager(data / "projects", db)
    Session._current = User("u1", "admin", "admin")
    win = MainWindow(db, pm, EventBus(), staging_dir=data / "staging")
    for w, h in sizes:
        win.resize(w, h)
        win.show()
        for i in range(win.tabs.count()):
            win.tabs.setCurrentIndex(i)
            app.processEvents()
            name = win.tabs.tabText(i).strip().replace(" ", "_")
            win.grab().save(str(out / f"{name}_{w}x{h}.png"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
