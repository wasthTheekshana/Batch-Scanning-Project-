"""Drive the real main(): start, sign out with old-session dialogs open, sign in again, close.

Runs headless against a throw-away data folder (the real one is not touched):  python scripts/smoke_signout.py
Prints what happened; tests/test_signout_flow.py checks the lines."""
import os, sys, tempfile
tmp = tempfile.mkdtemp()
os.environ["LOCALAPPDATA"] = tmp                     # a throw-away data folder: the real one is not touched
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["DOK_IDLE_MINUTES"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QDialog
import ui.login_dialog as ld
import main as m
log = []
def fake_exec(self):
    log.append("sign-in page shown")
    return QDialog.DialogCode.Accepted
ld.LoginDialog.exec = fake_exec
m.LoginDialog = ld.LoginDialog
m.require_password_change = lambda db, parent=None: True
extra = []
def open_extras():
    from PyQt6.QtWidgets import QDialog as D
    d = D(); d.setWindowTitle("old session dialog"); d.show(); extra.append(d)           # a non-modal window left open
    m_ = D(); m_.setWindowTitle("old session modal"); extra.append(m_)
    QTimer.singleShot(200, lambda: log.append("modal exec returned: %s" % m_.exec()))   # a modal one with its own loop
def step1():
    wins = [w for w in QApplication.topLevelWidgets() if w.__class__.__name__ == "MainWindow" and w.isVisible()]
    log.append(f"main windows open: {len(wins)}")
    open_extras()
    QTimer.singleShot(800, lambda: (log.append("extras visible before sign out: %s" % [e.isVisible() for e in extra]), wins[0].sign_out()))
def step2():
    wins = [w for w in QApplication.topLevelWidgets() if w.__class__.__name__ == "MainWindow" and w.isVisible()]
    log.append(f"after sign out and sign in again, main windows open: {len(wins)}; old dialogs still visible: {[e.isVisible() for e in extra]}")
    wins[0].close()                                  # closing the window quits
def schedule(self_):
    QTimer.singleShot(4000, step1)
    QTimer.singleShot(11000, step2)
m.HotFolderService.start = schedule
code = m.main()
print("\n".join(log)); print("exit code", code)
