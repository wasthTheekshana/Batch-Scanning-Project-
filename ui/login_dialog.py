"""Sign-in screen — the first panel shown on startup (HLD §17.5)."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QFrame, QLabel, QLineEdit,
                             QPushButton, QVBoxLayout)

from security.rbac import (DEFAULT_PASSWORD, MIN_PASSWORD_LENGTH, AuthError, Session,
                       change_password, validate_new_password)
from ui.theme import mark


class LoginDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.user = None
        self.setWindowTitle("DOK Scanner — Sign in")
        self.setFixedWidth(380)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 28, 28, 24)
        outer.setSpacing(10)

        brand = QLabel("DOK Scanner")
        brand.setStyleSheet("font-size: 18pt; font-weight: 700;")
        brand.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(brand)
        tagline = QLabel("Batch capture · Auto-QC · Delivery")
        mark(tagline, "muted")
        tagline.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(tagline)

        rule = QFrame()
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setStyleSheet("color: #D9DEE5;")
        outer.addWidget(rule)
        outer.addSpacing(6)

        user_label = QLabel("Username")
        mark(user_label, "muted")
        outer.addWidget(user_label)
        self.username = QLineEdit()
        self.username.setPlaceholderText("e.g. admin")
        outer.addWidget(self.username)

        pw_label = QLabel("Password")
        mark(pw_label, "muted")
        outer.addWidget(pw_label)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.returnPressed.connect(self.try_login)
        outer.addWidget(self.password)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #C0392B;")
        self.error_label.setWordWrap(True)
        outer.addWidget(self.error_label)

        self.signin_btn = QPushButton("Sign in")
        mark(self.signin_btn, "primary")
        self.signin_btn.setDefault(True)
        self.signin_btn.clicked.connect(self.try_login)
        outer.addWidget(self.signin_btn)

        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        outer.addWidget(cancel)

    def try_login(self) -> None:
        try:
            self.user = Session.login(self.username.text().strip(),
                                      self.password.text(), self.db)
            self.accept()
        except AuthError as e:
            self.error_label.setText(str(e))


class ChangePasswordDialog(QDialog):
    """Shown right after sign-in while the default password is still in use.
    There is no way to dismiss it other than choosing a new password or
    cancelling, which signs the user out."""

    def __init__(self, db, user, parent=None):
        super().__init__(parent)
        self.db = db
        self.user = user
        self.chosen = ""
        self.setWindowTitle("Choose a new password")
        self.setMinimumWidth(380)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(10)
        note = QLabel("You are using the default password. Choose a new one "
                      f"(at least {MIN_PASSWORD_LENGTH} characters) to continue.")
        note.setWordWrap(True)
        outer.addWidget(note)

        self.new_password = QLineEdit()
        self.new_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.new_password.setPlaceholderText("New password")
        outer.addWidget(self.new_password)
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm.setPlaceholderText("Repeat new password")
        self.confirm.returnPressed.connect(self.try_change)
        outer.addWidget(self.confirm)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #C0392B;")
        self.error_label.setWordWrap(True)
        outer.addWidget(self.error_label)

        self.save_btn = QPushButton("Save and continue")
        mark(self.save_btn, "primary")
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self.try_change)
        outer.addWidget(self.save_btn)
        cancel = QPushButton("Cancel and sign out")
        cancel.clicked.connect(self.reject)
        outer.addWidget(cancel)

    def try_change(self) -> None:
        pw = self.new_password.text()
        if pw != self.confirm.text():
            self.error_label.setText("The two passwords do not match.")
            return
        try:
            validate_new_password(pw, self.user.username)
            change_password(self.db, self.user.username, DEFAULT_PASSWORD, pw)
        except (ValueError, AuthError) as e:
            self.error_label.setText(str(e))
            return
        self.chosen = pw
        self.accept()


def require_password_change(db, parent=None) -> bool:
    """After sign-in: if the user is still on the default password make them
    change it. False means they declined; the session has been closed."""
    user = Session.current_user()
    if user is None or not user.must_change_password:
        return True
    dlg = ChangePasswordDialog(db, user, parent)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        Session.login(user.username, dlg.chosen, db)   # refresh the session
        return True
    Session.logout(db)
    return False
