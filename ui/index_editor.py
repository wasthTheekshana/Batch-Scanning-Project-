"""Index tab — manual indexing work queue (HLD §4 Layer 5).

Left: the documents needing index attention (auto-extract confidence
below threshold, or no fields yet). Right: the page image next to the
field form so the operator can read values straight off the scan, plus
one-click re-extraction of the configured zones. Saving updates the
document, clears its low-confidence flag, and writes the audit trail.
"""
from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QCompleter, QFormLayout, QHBoxLayout,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem,
                             QPushButton, QSplitter, QVBoxLayout, QWidget)

from ui.image_viewer import ImageView
from ui.theme import STATUS_COLORS, mark


class IndexEditor(QWidget):
    fields_saved = pyqtSignal(str, dict)  # document_id, fields

    def __init__(self, db, zones: list[dict], audit=None,
                 current_user=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.audit = audit
        self.current_user = current_user
        self.zones = list(zones or [])
        self.document_id: str | None = None
        self._inputs: dict[str, QLineEdit] = {}

        layout = QHBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # -- left: work queue -------------------------------------------------
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(QLabel("Documents"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItem("Needs indexing", "needs")
        self.filter_combo.addItem("All documents", "all")
        self.filter_combo.currentIndexChanged.connect(lambda _: self.refresh())
        ll.addWidget(self.filter_combo)
        self.doc_list = QListWidget()
        self.doc_list.currentItemChanged.connect(self._on_select)
        ll.addWidget(self.doc_list)
        splitter.addWidget(left)

        # -- right: page + fields ----------------------------------------------
        right = QWidget()
        rl = QHBoxLayout(right)
        self.page_view = ImageView()
        rl.addWidget(self.page_view, stretch=3)

        form_col = QVBoxLayout()
        self.title = QLabel("Select a document from the list.")
        mark(self.title, "muted")
        self.title.setWordWrap(True)
        form_col.addWidget(self.title)
        self.form = QFormLayout()
        form_col.addLayout(self.form)

        self.extract_btn = QPushButton("Auto-extract from page")
        self.extract_btn.clicked.connect(self.auto_extract)
        form_col.addWidget(self.extract_btn)
        self.save_button = QPushButton("Save index fields")
        mark(self.save_button, "primary")
        self.save_button.clicked.connect(self.save)
        form_col.addWidget(self.save_button)
        self.hint = QLabel("")
        mark(self.hint, "muted")
        self.hint.setWordWrap(True)
        form_col.addWidget(self.hint)
        form_col.addStretch(1)
        rl.addLayout(form_col, stretch=2)
        splitter.addWidget(right)
        splitter.setSizes([280, 900])

        self.set_zones(self.zones)
        self.refresh()

    # -- configuration ---------------------------------------------------------
    def set_zones(self, zones: list[dict]) -> None:
        """Rebuild the field form from the project's index zones."""
        self.zones = list(zones or [])
        while self.form.rowCount():
            self.form.removeRow(0)
        self._inputs.clear()
        for zone in self.zones:
            edit = QLineEdit()
            edit.setCompleter(self._completer_for(zone["name"]))
            self._inputs[zone["name"]] = edit
            self.form.addRow(zone["name"], edit)
        if not self.zones:
            self.hint.setText(
                "No index zones configured for the active project. "
                "Define zones in Projects → Index zones, then new scans "
                "auto-extract these fields.")
        else:
            self.hint.setText("")

    def _completer_for(self, field: str) -> QCompleter:
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

    # -- work queue -------------------------------------------------------------
    def _needs_indexing(self, doc) -> bool:
        if doc["validation_status"] == "rejected":
            return False
        try:
            detail = json.loads(doc["qc_detail"] or "{}")
            fields = json.loads(doc["extracted_fields"] or "{}")
        except json.JSONDecodeError:
            return False
        if "low_confidence_index" in detail.get("flags", []):
            return True
        return self.zones and not any(
            fields.get(z["name"]) for z in self.zones)

    def refresh(self) -> None:
        self.doc_list.clear()
        mode = self.filter_combo.currentData()
        for doc in self.db.list_documents():
            if mode == "needs" and not self._needs_indexing(doc):
                continue
            item = QListWidgetItem(
                f"{doc['document_id'][:8]} · pages {doc['page_range']} · "
                f"{doc['validation_status']}")
            item.setData(Qt.ItemDataRole.UserRole, doc["document_id"])
            from PyQt6.QtGui import QBrush, QColor
            color = STATUS_COLORS.get(doc["validation_status"])
            if color:
                item.setForeground(QBrush(QColor(color)))
            self.doc_list.addItem(item)

    def _on_select(self, current, _previous=None) -> None:
        if current is not None:
            self.load_document(current.data(Qt.ItemDataRole.UserRole))

    # -- document ------------------------------------------------------------------
    def _first_page_image(self, doc, prefer_original: bool = False) -> str | None:
        """First page image path. prefer_original=True returns the raw
        scan — zone coordinates are defined against original geometry,
        so extraction must NOT use the cropped/deskewed working copy."""
        try:
            pages = json.loads(doc["qc_detail"] or "{}").get("pages", {})
        except json.JSONDecodeError:
            return None
        order = (("original_path", "working_path") if prefer_original
                 else ("working_path", "original_path"))
        for no in sorted(pages, key=lambda k: int(k) if str(k).isdigit() else 0):
            info = pages[no]
            if isinstance(info, dict):
                for key in order:
                    path = info.get(key)
                    if path and Path(path).exists():
                        return path
        return None

    def load_document(self, document_id: str) -> None:
        doc = self.db.get_document(document_id)
        if doc is None:
            return
        self.document_id = document_id
        self.title.setText(f"Document {document_id[:8]} · "
                           f"pages {doc['page_range']} · "
                           f"{doc['validation_status']}")
        fields = json.loads(doc["extracted_fields"] or "{}")
        for name, edit in self._inputs.items():
            edit.setText(str(fields.get(name, "")))
        image = self._first_page_image(doc)
        if image:
            self.page_view.set_image(image)

    def auto_extract(self) -> None:
        """Re-run zone extraction on this document's first page — useful
        after zones were added to the project."""
        if self.document_id is None or not self.zones:
            self.hint.setText("Select a document and configure zones first.")
            return
        doc = self.db.get_document(self.document_id)
        # extraction must use the ORIGINAL page: zones are drawn against
        # original geometry, and the working copy is cropped/deskewed
        image_path = self._first_page_image(doc, prefer_original=True)
        if not image_path:
            self.hint.setText("No page image available for this document.")
            return
        try:
            import cv2

            from ocr.tesseract_engine import TesseractNotFound
            from ocr.zone_extractor import extract_fields
            image = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
            fields = extract_fields(image, self.zones)
        except TesseractNotFound:
            self.hint.setText("Tesseract OCR is not available on this "
                              "machine — enter the fields manually.")
            return
        except Exception as e:
            self.hint.setText(f"Extraction failed: {e}")
            return
        results = []
        for name, fv in fields.items():
            if name not in self._inputs:
                continue
            if fv.value:
                self._inputs[name].setText(fv.value)
                results.append(f"{name}: “{fv.value}” "
                               f"({fv.confidence:.0%})")
            else:
                results.append(f"{name}: no match — check the zone "
                               "position/regex (Projects → Index zones "
                               "→ Test zone)")
        self.hint.setText("\n".join(results) if results else
                          "No zone fields configured.")

    def save(self) -> None:
        if self.document_id is None:
            return
        doc = self.db.get_document(self.document_id)
        fields = json.loads(doc["extracted_fields"] or "{}")
        fields.update({name: edit.text().strip()
                       for name, edit in self._inputs.items()})
        detail = json.loads(doc["qc_detail"] or "{}")
        flags = [f for f in detail.get("flags", [])
                 if f != "low_confidence_index"]
        detail["flags"] = flags
        self.db.update_document(self.document_id, extracted_fields=fields,
                                qc_detail=detail)
        user = self.current_user.username if self.current_user else "unknown"
        if self.audit:
            self.audit.log("index_edit", user, document_id=self.document_id,
                           fields=list(self._inputs))
        self.fields_saved.emit(self.document_id, fields)
        self.hint.setText("Saved.")
        self.refresh()
