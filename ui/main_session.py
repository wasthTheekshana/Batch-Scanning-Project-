"""The window's session: where it opens, the idle lock, closing and signing out.

A mixin of ui.main_window.MainWindow: it relies on the window's `db` and its `closed` signal.
"""
from __future__ import annotations

from security.rbac import Session
from ui.theme import fit_to_screen

DEFAULT_W, DEFAULT_H = 1360, 860


class SessionMixin:
    def restore_geometry_or_default(self) -> None:
        """Open where the operator left the window (if that is still on a screen),
        otherwise at a size that suits this screen; remember it from now on."""
        from PyQt6.QtCore import QSettings
        self._remember_geometry = True
        saved = QSettings("DOKScanner", "DOKScanner").value("window/geometry")
        if saved is not None and self.restoreGeometry(saved):
            return
        fit_to_screen(self, DEFAULT_W, DEFAULT_H, share=0.94)

    def start_idle_lock(self, app, minutes: float | None = None) -> None:
        """Lock the session after `minutes` without keyboard or mouse use (0 = never)."""
        from ui.idle_lock import IdleGuard, configured_minutes
        self._idle = IdleGuard(configured_minutes() if minutes is None else minutes, self)
        self._idle.idle.connect(self.lock_session)
        self._idle.start(app)

    def lock_session(self) -> None:
        """Cover the window until the signed-in user types their password; a wrong
        password three times, or Sign out, ends the session."""
        from ui.idle_lock import UnlockDialog
        user = Session.current_user()
        if user is None or getattr(self, "_locked", False):
            return
        self._locked = True
        self.db.append_audit(None, user.username, "session_locked", {})
        try:
            dialog = UnlockDialog(self.db, user.username, self)
            ok = dialog.exec() == dialog.DialogCode.Accepted
        finally:
            self._locked = False
        if ok:
            self.db.append_audit(None, user.username, "session_unlocked", {})
            if getattr(self, "_idle", None) is not None:
                self._idle.rearm()
        else:
            self.sign_out()

    def closeEvent(self, event) -> None:
        if getattr(self, "_remember_geometry", False):
            from PyQt6.QtCore import QSettings
            QSettings("DOKScanner", "DOKScanner").setValue("window/geometry", self.saveGeometry())
        super().closeEvent(event)
        if event.isAccepted():
            self.closed.emit(self._signed_out)

    def sign_out(self) -> None:
        """End the session. The program stays open and shows the sign-in page again (see main.py); closing the
        window any other way quits the program."""
        Session.logout(self.db)
        self._signed_out = True
        self.close()
