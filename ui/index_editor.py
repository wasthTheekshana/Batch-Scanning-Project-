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
                 current_user=None, parent=None, full_page: bool = False,
                 zones_for_project=None):
        super().__init__(parent)
        # project_id -> (zones, full_page) or None; lets each document be
        # indexed with ITS project's zones, not the first project's
        self._zones_for_project = zones_for_project
        self.db = db
        self.audit = audit
        self.current_user = current_user
        self.zones = list(zones or [])
        self.full_page = full_page
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
    def set_zones(self, zones: list[dict], full_page: bool | None = None) -> None:
        """Rebuild the field form from the project's index zones.
        full_page=None keeps the current mode (e.g. an initial call from
        __init__ already passed it in); pass explicitly to change it."""
        self.zones = list(zones or [])
        if full_page is not None:
            self.full_page = full_page
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

    def _project_zones(self, project_id) -> tuple[list[dict], bool]:
        """The zones/mode for a document's own project; the editor's
        current zones when no lookup is wired or the project is unknown."""
        if self._zones_for_project and project_id:
            found = self._zones_for_project(project_id)
            if found is not None:
                return list(found[0] or []), bool(found[1])
        return self.zones, self.full_page

    def _completer_for(self, field: str) -> QCompleter:
        return QCompleter(sorted(self.db.known_field_values(field)))

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
        zones, _full_page = self._project_zones(doc["project_id"])
        return bool(zones) and not any(
            fields.get(z["name"]) for z in zones)

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
        zones, full_page = self._project_zones(doc["project_id"])
        if zones != self.zones or full_page != self.full_page:
            self.set_zones(zones, full_page)
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
        # extraction uses ORIGINAL pages: zones are drawn against original
        # geometry (the working copy is cropped/deskewed); zones with
        # ref dimensions scale automatically to each page's resolution
        page_paths = self._page_originals(doc)
        if not page_paths:
            self.hint.setText("No page images available for this document.")
            return
        best: dict[str, tuple[str, float, str]] = {}  # name -> (value, conf, page)
        try:
            import cv2

            from ocr.tesseract_engine import TesseractNotFound, ocr_page
            from ocr.zone_extractor import (extract_fields,
                                            extract_fields_from_text)
            for page_no, path in page_paths:
                image = cv2.imread(path, cv2.IMREAD_UNCHANGED)
                if image is None:
                    continue
                if self.full_page:
                    result = ocr_page(image)
                    fields = extract_fields_from_text(
                        result.text, result.confidence, self.zones)
                else:
                    fields = extract_fields(image, self.zones)
                for name, fv in fields.items():
                    if fv.value and name not in best:
                        best[name] = (fv.value, fv.confidence, page_no)
                if len(best) == len(self.zones):
                    break  # every field found
        except TesseractNotFound:
            self.hint.setText("Tesseract OCR is not available on this "
                              "machine — enter the fields manually.")
            return
        except Exception as e:
            self.hint.setText(f"Extraction failed: {e}")
            return
        results = []
        for zone in self.zones:
            name = zone["name"]
            if name not in self._inputs:
                continue
            if name in best:
                value, conf, page_no = best[name]
                self._inputs[name].setText(value)
                results.append(f"{name}: “{value}” ({conf:.0%}, "
                               f"page {page_no})")
            else:
                results.append(f"{name}: no match on any page — check the "
                               "zone position/regex (Projects → Index "
                               "zones → Test zone)")
        self.hint.setText("\n".join(results) if results else
                          "No zone fields configured.")

    def _page_originals(self, doc) -> list[tuple[str, str]]:
        """[(page_no, original_image_path)] for every page on disk."""
        try:
            pages = json.loads(doc["qc_detail"] or "{}").get("pages", {})
        except json.JSONDecodeError:
            return []
        out = []
        for no in sorted(pages, key=lambda k: int(k) if str(k).isdigit() else 0):
            info = pages[no]
            if isinstance(info, dict):
                path = info.get("original_path") or info.get("working_path")
                if path and Path(path).exists():
                    out.append((no, path))
        return out

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
