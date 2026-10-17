"""Locks the session after a period with no keyboard or mouse use.

A workstation left signed in at a bank counter is a risk, so after IDLE_MINUTES without
input the window is covered by a dialog that asks for the signed-in user's password again.
Three wrong passwords, or Cancel, sign the user out. Wrong passwords count towards the same
account lock as at sign-in (core.rbac).
"""
from __future__ import annotations

import os

from PyQt6.QtCore import QEvent, QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QLineEdit, QVBoxLayout)

from core.rbac import verify_session_password

IDLE_MINUTES = 15
MAX_UNLOCK_TRIES = 3
_ACTIVITY = {QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove, QEvent.Type.KeyPress,
             QEvent.Type.Wheel, QEvent.Type.TouchBegin}


def configured_minutes() -> float:
    """DOK_IDLE_MINUTES overrides the default; 0 turns the lock off."""
    try:
        return float(os.environ.get("DOK_IDLE_MINUTES", IDLE_MINUTES))
    except ValueError:
        return IDLE_MINUTES


class IdleGuard(QObject):
    """Emits `idle` once after `minutes` without user input. Re-armed by `rearm()`."""
    idle = pyqtSignal()

    def __init__(self, minutes: float, parent=None):
        super().__init__(parent)
        self.minutes = minutes
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.idle.emit)

    def start(self, app) -> None:
        if self.minutes > 0:
            app.installEventFilter(self)
            self.rearm()

    def stop(self, app=None) -> None:
        self._timer.stop()
        if app is not None:
            app.removeEventFilter(self)

    def rearm(self) -> None:
        if self.minutes > 0:
            self._timer.start(int(self.minutes * 60_000))

    def eventFilter(self, obj, event) -> bool:
        if event.type() in _ACTIVITY:
            self.rearm()
        return False


class UnlockDialog(QDialog):
    """Asks the signed-in user for their password; accepted only when it is right."""

    def __init__(self, db, username: str, parent=None):
        super().__init__(parent)
        self.db, self.username, self.tries = db, username, 0
        self.setWindowTitle("Session locked")
        self.setModal(True)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"This session was locked after {IDLE_MINUTES} minutes without "
                                f"activity.\nEnter the password for {username} to continue."))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.returnPressed.connect(self.try_unlock)
        layout.addWidget(self.password)
        self.message = QLabel("")
        layout.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        self.sign_out = buttons.addButton("Sign out", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.try_unlock)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def try_unlock(self) -> None:
        if verify_session_password(self.db, self.username, self.password.text()):
            self.accept()
            return
        self.tries += 1
        self.password.clear()
        if self.tries >= MAX_UNLOCK_TRIES:
            self.reject()
            return
        self.message.setText(f"Wrong password ({MAX_UNLOCK_TRIES - self.tries} tries left).")

    def reject(self) -> None:          # also the close button
        super().reject()
