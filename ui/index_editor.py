"""Manual index editor — QFormLayout built from the project's OCR zones,
with SQLite-backed auto-complete (HLD §4 Layer 5, §16)."""
from __future__ import annotations

import json

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QCompleter, QFormLayout, QLabel, QLineEdit,
                             QPushButton, QVBoxLayout, QWidget)


class IndexEditor(QWidget):
    fields_saved = pyqtSignal(str, dict)  # document_id, fields

    def __init__(self, db, zones: list[dict], audit=None,
                 current_user=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.audit = audit
        self.current_user = current_user
        self.document_id: str | None = None
        self._inputs: dict[str, QLineEdit] = {}

        layout = QVBoxLayout(self)
        self.title = QLabel("No document selected")
        layout.addWidget(self.title)
        form = QFormLayout()
        for zone in zones:
            edit = QLineEdit()
            edit.setCompleter(self._completer_for(zone["name"]))
            self._inputs[zone["name"]] = edit
            form.addRow(zone["name"], edit)
        layout.addLayout(form)
        self.save_button = QPushButton("Save index fields")
        self.save_button.clicked.connect(self.save)
        layout.addWidget(self.save_button)
        layout.addStretch(1)

    def _completer_for(self, field: str) -> QCompleter:
        """Auto-complete from values previously stored for this field."""
        values = set()
        for row in self.db.conn.execute(
                "SELECT extracted_fields FROM documents").fetchall():
            try:
                v = json.loads(row["extracted_fields"] or "{}").get(field)
                if v:
                    values.add(str(v))
            except json.JSONDecodeError:
                continue
        return QCompleter(sorted(values))

    def load_document(self, document_id: str) -> None:
        doc = self.db.get_document(document_id)
        if doc is None:
            return
        self.document_id = document_id
        self.title.setText(f"Document {document_id[:8]} · "
                           f"pages {doc['page_range']}")
        fields = json.loads(doc["extracted_fields"] or "{}")
        for name, edit in self._inputs.items():
            edit.setText(str(fields.get(name, "")))

    def save(self) -> None:
        if self.document_id is None:
            return
        doc = self.db.get_document(self.document_id)
        fields = json.loads(doc["extracted_fields"] or "{}")
        fields.update({name: edit.text().strip()
                       for name, edit in self._inputs.items()})
        self.db.update_document(self.document_id, extracted_fields=fields)
        user = self.current_user.username if self.current_user else "unknown"
        if self.audit:
            self.audit.log("index_edit", user, document_id=self.document_id,
                           fields=list(self._inputs))
        self.fields_saved.emit(self.document_id, fields)
