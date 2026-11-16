"""The two blocks of the sidebar: the product name on top, who is signed in below."""
from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

ROLE_LABELS = {"admin": "Administrator", "operator": "Operator",
               "qc_officer": "QC Officer", "viewer": "Viewer"}


class BrandBlock(QWidget):
    """The product name at the top of the sidebar (just the mark when the rail is narrow)."""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(0)
        self.title = QLabel("DOK Scanner")
        self.title.setObjectName("HeaderTitle")
        self.sub = QLabel("Capture · QC · Delivery")
        self.sub.setObjectName("HeaderSub")
        layout.addWidget(self.title)
        layout.addWidget(self.sub)

    def set_compact(self, compact: bool) -> None:
        self.title.setText("DOK" if compact else "DOK Scanner")
        self.sub.setVisible(not compact)


class UserBlock(QWidget):
    """Who is signed in, and Sign out, at the bottom of the sidebar."""

    def __init__(self, who: str | None):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.chip = QLabel(who or "")
        self.chip.setObjectName("UserChip")
        self.chip.setWordWrap(True)
        self.chip.setVisible(bool(who))
        self.signout_btn = QPushButton("Sign out")
        self.signout_btn.setObjectName("SignOut")
        layout.addWidget(self.chip)
        layout.addWidget(self.signout_btn)
        self._who = who

    def set_compact(self, compact: bool) -> None:
        self.chip.setVisible(bool(self._who) and not compact)
        self.signout_btn.setText("⏻" if compact else "Sign out")
        self.signout_btn.setToolTip("Sign out")
