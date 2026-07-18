"""Sign-in screen — the first panel shown on startup (HLD §17.5)."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QFrame, QLabel, QLineEdit,
                             QPushButton, QVBoxLayout)

from core.rbac import AuthError, Session
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
