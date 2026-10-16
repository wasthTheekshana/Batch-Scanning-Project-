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
from PyQt6.QtWidgets import (QComboBox, QCompleter, QFormLayout, QGroupBox, QHBoxLayout,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem,
                             QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget)

from core.validation import kind_for, validate
from ui.image_viewer import ImageView
from ui.theme import STATUS_COLORS, mark


class IndexEditor(QWidget):
    fields_saved = pyqtSignal(str, dict)  # document_id, fields
    page_types_saved = pyqtSignal(str)    # document_id

    def __init__(self, db, zones: list[dict], audit=None,
                 current_user=None, parent=None, full_page: bool = False,
                 zones_for_project=None, ocr_cfg_for_project=None):
        super().__init__(parent)
        # project_id -> the project's ocr config (document types), so each
        # page is extracted with the zones of its own document type
        self._ocr_cfg_for_project = ocr_cfg_for_project
        self._ocr_cfg: dict | None = None
        # project_id -> (zones, full_page) or None; lets each document be
        # indexed with ITS project's zones, not the first project's
        self._zones_for_project = zones_for_project
        self.db = db
        self.audit = audit
        self.current_user = current_user
        self.zones = list(zones or [])
        self.full_page = full_page
        self.document_id: str | None = None
        self._low_conf: set[str] = set()      # fields the reader was unsure of
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
        self.filter_combo.addItem("Fields to check (low confidence)", "lowconf")
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
        # the type of each page (NIC, Application, ...): drives the PDF bookmarks
        self.types_box = QGroupBox("Page types")
        self.types_form = QFormLayout(self.types_box)
        self.save_types_btn = QPushButton("Save page types")
        self.save_types_btn.clicked.connect(self.save_page_types)
        self._type_combos: dict[str, QComboBox] = {}
        self._can_edit_types = current_user is not None and (
            current_user.can("scan.run") or current_user.can("qc.review"))
        form_col.addWidget(self.types_box)
        form_col.addWidget(self.save_types_btn)
        self.types_box.hide()
        self.save_types_btn.hide()
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
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        """Saving and extracting need a selected document and something to fill."""
        has = self.document_id is not None
        self.save_button.setEnabled(has and bool(self._inputs))
        self.extract_btn.setEnabled(has and bool(self.zones))

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
            edit.textChanged.connect(
                lambda _t, n=zone["name"]: self._check_field(n))
            self.form.addRow(zone["name"], edit)
        if not self.zones:
            self.hint.setText(
                "No index zones configured for the active project. "
                "Add index fields in the Projects tab (Workflow Designer), then new scans "
                "auto-extract these fields.")
        else:
            self.hint.setText("")

    # -- format checks ---------------------------------------------------------
    def _kind(self, name: str) -> str | None:
        zone = next((z for z in self.zones if z.get("name") == name), {})
        return kind_for(zone)

    def _check_field(self, name: str) -> str | None:
        """Mark a field whose value looks wrong (red border and a tooltip)."""
        edit = self._inputs[name]
        message = validate(self._kind(name), edit.text())
        unsure = name in self._low_conf and not message
        edit.setStyleSheet("border: 1px solid #d33;" if message
                           else "background: #fff4cc; color: #222;" if unsure else "")
        edit.setToolTip(message or ("Read with low confidence: please check "
                                    "this against the page." if unsure else ""))
        return message

    def invalid_fields(self) -> dict[str, str]:
        return {n: m for n in self._inputs if (m := self._check_field(n))}

    def _confirm_invalid(self, problems: dict[str, str]) -> bool:
        lines = "\n".join(f"{n}: {m}" for n, m in problems.items())
        answer = QMessageBox.question(
            self, "Check these values",
            f"These values look wrong:\n\n{lines}\n\nSave them anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

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

    @staticmethod
    def _to_check(doc) -> int:
        """How many fields the reader was unsure of and nobody has reviewed."""
        try:
            return len(json.loads(doc["qc_detail"] or "{}").get("low_conf_fields") or [])
        except (TypeError, ValueError):
            return 0

    def refresh(self) -> None:
        self.doc_list.clear()
        mode = self.filter_combo.currentData()
        for doc in self.db.list_documents():
            if mode == "needs" and not self._needs_indexing(doc):
                continue
            if mode == "lowconf" and not self._to_check(doc):
                continue
            check = self._to_check(doc)
            item = QListWidgetItem(
                f"{doc['document_id'][:8]} · pages {doc['page_range']} · "
                f"{doc['validation_status']}"
                + (f" · {check} field(s) to check" if check else ""))
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

    def select_document(self, document_id: str) -> bool:
        """Show this document in the list (whatever the filter) and open it.
        False when it is not there."""
        self.filter_combo.setCurrentIndex(self.filter_combo.findData("all"))
        self.refresh()
        for row in range(self.doc_list.count()):
            if self.doc_list.item(row).data(Qt.ItemDataRole.UserRole) == document_id:
                self.doc_list.setCurrentRow(row)
                return True
        return False

    def load_document(self, document_id: str) -> None:
        doc = self.db.get_document(document_id)
        if doc is None:
            return
        self.document_id = document_id
        zones, full_page = self._project_zones(doc["project_id"])
        if zones != self.zones or full_page != self.full_page:
            self.set_zones(zones, full_page)
        self._sync_buttons()
        self._ocr_cfg = (self._ocr_cfg_for_project(doc["project_id"])
                         if self._ocr_cfg_for_project else None)
        self.title.setText(f"Document {document_id[:8]} · "
                           f"pages {doc['page_range']} · "
                           f"{doc['validation_status']}")
        self._load_page_types(doc)
        fields = json.loads(doc["extracted_fields"] or "{}")
        try:
            self._low_conf = set(json.loads(doc["qc_detail"] or "{}")
                                 .get("low_conf_fields") or [])
        except (TypeError, ValueError):
            self._low_conf = set()
        for name, edit in self._inputs.items():
            edit.setText(str(fields.get(name, "")))
            self._check_field(name)
        image = self._first_page_image(doc)
        if image:
            self.page_view.set_image(image)

    # -- page types ------------------------------------------------------------
    def _load_page_types(self, doc) -> None:
        """One drop-down per page, with the project's document types."""
        while self.types_form.rowCount():
            self.types_form.removeRow(0)
        self._type_combos.clear()
        from ocr.doc_types import document_types
        names = [t["name"] for t in document_types(self._ocr_cfg or {})]
        try:
            pages = json.loads(doc["qc_detail"] or "{}").get("pages", {})
        except (TypeError, ValueError):
            pages = {}
        pages = {k: v for k, v in pages.items() if isinstance(v, dict)}
        show = bool(names and pages)
        self.types_box.setVisible(show)
        self.save_types_btn.setVisible(show)
        self.save_types_btn.setEnabled(show and self._can_edit_types)
        if not show:
            return
        for page_no in sorted(pages, key=lambda k: int(k) if str(k).isdigit() else 0):
            current = pages[page_no].get("doc_type") or ""
            combo = QComboBox()
            combo.addItem("(no type)", "")
            for name in names + ([current] if current and current not in names else []):
                combo.addItem(name, name)
            combo.setCurrentIndex(max(0, combo.findData(current)))
            combo.setEnabled(self._can_edit_types)
            self._type_combos[page_no] = combo
            self.types_form.addRow(f"Page {page_no}", combo)

    def save_page_types(self) -> None:
        """Store the corrected page types, then let the window rewrite the
        exported PDF's bookmarks and redeliver (see Pipeline.reissue_page_types)."""
        if self.document_id is None or not self._can_edit_types or not self._type_combos:
            return
        doc = self.db.get_document(self.document_id)
        detail = json.loads(doc["qc_detail"] or "{}")
        pages = detail.get("pages", {})
        changed = []
        for page_no, combo in self._type_combos.items():
            chosen = combo.currentData() or None
            info = pages.get(page_no)
            if not isinstance(info, dict) or (info.get("doc_type") or None) == chosen:
                continue
            if chosen:
                info["doc_type"] = chosen
            else:
                info.pop("doc_type", None)
            changed.append(page_no)
        if not changed:
            self.hint.setText("No page type was changed.")
            return
        order = sorted(pages, key=lambda k: int(k) if str(k).isdigit() else 0)
        found = list(dict.fromkeys(pages[n].get("doc_type") for n in order
                                   if isinstance(pages[n], dict) and pages[n].get("doc_type")))
        fields = json.loads(doc["extracted_fields"] or "{}")
        if found:
            fields["doc_types"] = ", ".join(found)
        else:
            fields.pop("doc_types", None)
        self.db.update_document(self.document_id, qc_detail=detail, extracted_fields=fields)
        user = self.current_user.username if self.current_user else "unknown"
        if self.audit:
            self.audit.log("page_types_edit", user, document_id=self.document_id,
                           pages=sorted(changed, key=int))
        self.page_types_saved.emit(self.document_id)
        self.hint.setText(f"Page types saved ({len(changed)} page(s) changed).")

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
            from ocr.doc_types import document_types, page_zone_groups
            from ocr.zone_extractor import (extract_fields,
                                            extract_fields_from_text)
            typed = bool(self._ocr_cfg and document_types(self._ocr_cfg))
            project_mode = "full_page" if self.full_page else "zone"
            from ocr.orientation import turn
            rotations = self._page_rotations(doc)
            for page_no, path in page_paths:
                image = cv2.imread(path, cv2.IMREAD_UNCHANGED)
                if image is None:
                    continue
                image = turn(image, rotations.get(page_no, 0))
                if typed:      # this page's zones: common + its document type
                    result = ocr_page(image)
                    groups, _name = page_zone_groups(self._ocr_cfg, result.text)
                else:
                    groups = [(self.zones, project_mode)]
                    result = ocr_page(image) if self.full_page else None
                fields = {}
                for zones, mode in groups:
                    if mode == "full_page":
                        fields.update(extract_fields_from_text(
                            result.text, result.confidence, zones))
                    else:
                        page_words = getattr(result, "words", None)
                        fields.update(extract_fields(
                            image, zones,
                            **({"words": page_words} if page_words else {})))
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

    def _page_rotations(self, doc) -> dict[str, int]:
        """page_no -> clockwise degrees that make that page upright (pages
        that were fed the wrong way up are read upright, as the pipeline did)."""
        try:
            pages = json.loads(doc["qc_detail"] or "{}").get("pages", {})
        except json.JSONDecodeError:
            return {}
        return {no: int(info.get("rotation") or 0) for no, info in pages.items()
                if isinstance(info, dict)}

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
        problems = self.invalid_fields()
        if problems and not self._confirm_invalid(problems):
            return
        doc = self.db.get_document(self.document_id)
        fields = json.loads(doc["extracted_fields"] or "{}")
        fields.update({name: edit.text().strip()
                       for name, edit in self._inputs.items()})
        detail = json.loads(doc["qc_detail"] or "{}")
        flags = [f for f in detail.get("flags", [])
                 if f != "low_confidence_index"]
        detail["flags"] = flags
        detail.pop("low_conf_fields", None)       # the operator has reviewed them
        self._low_conf = set()
        self.db.update_document(self.document_id, extracted_fields=fields,
                                qc_detail=detail)
        user = self.current_user.username if self.current_user else "unknown"
        if self.audit:
            self.audit.log("index_edit", user, document_id=self.document_id,
                           fields=list(self._inputs))
        self.fields_saved.emit(self.document_id, fields)
        self.hint.setText("Saved.")
        for name in self._inputs:
            self._check_field(name)
        self.refresh()
