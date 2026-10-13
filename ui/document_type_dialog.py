"""Dialog for one document type of a project (NIC, Application, Letter...):
its name, the pattern that recognises its pages, and its index fields."""
from __future__ import annotations

import re

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem,
                             QMessageBox, QPushButton, QVBoxLayout, QHBoxLayout)

from ui.theme import mark
from ui.zone_dialog import ZoneDialog


class DocumentTypeDialog(QDialog):
    def __init__(self, doc_type: dict | None = None, parent=None,
                 full_page: bool = True, taken_names: tuple = ()):
        super().__init__(parent)
        self.setWindowTitle("Document type")
        self.resize(560, 520)
        self.full_page = full_page
        self._taken = {n.lower() for n in taken_names}
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.name = QLineEdit(doc_type["name"] if doc_type else "")
        self.name.setPlaceholderText("e.g. NIC")
        form.addRow("Type name", self.name)
        self.regex = QLineEdit(doc_type.get("regex", "") if doc_type else "")
        self.regex.setPlaceholderText(
            "e.g. national identity card|identity card no")
        form.addRow("Recognised when the page text matches", self.regex)
        layout.addLayout(form)
        hint = QLabel(
            "A page belongs to the first type whose pattern matches its text "
            "(capital letters do not matter; use | for alternatives). Pages "
            "that match no type, such as the back of an ID card, follow the "
            "type of the page before them. Give every field a name that is "
            "unique across the project's types.")
        hint.setWordWrap(True)
        mark(hint, "muted")
        layout.addWidget(hint)

        layout.addWidget(QLabel("Index fields of this type"))
        self.zone_list = QListWidget()
        layout.addWidget(self.zone_list)
        for zone in (doc_type.get("zones") or []) if doc_type else []:
            self._append_zone(zone)
        buttons = QHBoxLayout()
        self.add_zone_btn = QPushButton("Add field…")
        self.edit_zone_btn = QPushButton("Edit…")
        self.del_zone_btn = QPushButton("Remove")
        self.add_zone_btn.clicked.connect(self.add_zone)
        self.edit_zone_btn.clicked.connect(self.edit_zone)
        self.del_zone_btn.clicked.connect(
            lambda: self.zone_list.takeItem(self.zone_list.currentRow()))
        for b in (self.add_zone_btn, self.edit_zone_btn, self.del_zone_btn):
            buttons.addWidget(b)
        layout.addLayout(buttons)

        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.error)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                               QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    # -- fields ----------------------------------------------------------------
    @staticmethod
    def _label(zone: dict) -> str:
        return zone["name"] + (f" · /{zone['regex']}/" if zone.get("regex") else "")

    def _append_zone(self, zone: dict) -> None:
        item = QListWidgetItem(self._label(zone))
        item.setData(Qt.ItemDataRole.UserRole, zone)
        self.zone_list.addItem(item)

    def add_zone(self) -> None:
        dlg = ZoneDialog(parent=self, full_page=self.full_page)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            zone = dlg.build_zone()
            if zone:
                self._append_zone(zone)

    def edit_zone(self) -> None:
        item = self.zone_list.currentItem()
        if item is None:
            return
        dlg = ZoneDialog(item.data(Qt.ItemDataRole.UserRole), parent=self,
                         full_page=self.full_page)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            zone = dlg.build_zone()
            if zone:
                item.setData(Qt.ItemDataRole.UserRole, zone)
                item.setText(self._label(zone))

    # -- result ----------------------------------------------------------------
    def zones(self) -> list[dict]:
        return [self.zone_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.zone_list.count())]

    def problem(self) -> str:
        """Why this type cannot be saved yet, or '' when it can."""
        name = self.name.text().strip()
        if not name:
            return "Give the document type a name."
        if name.lower() in self._taken:
            return f"There is already a document type called {name}."
        pattern = self.regex.text().strip()
        if not pattern:
            return ("Enter the pattern that recognises this type's pages "
                    "(for example: national identity card).")
        try:
            re.compile(pattern)
        except re.error:
            return "The recognition pattern is not a valid regular expression."
        if not self.zones():
            return "Add at least one index field."
        return ""

    def _accept(self) -> None:
        message = self.problem()
        self.error.setText(message)
        if not message:
            self.accept()

    def build_type(self) -> dict:
        return {"name": self.name.text().strip(),
                "regex": self.regex.text().strip(), "zones": self.zones()}
