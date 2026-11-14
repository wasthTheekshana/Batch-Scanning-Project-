"""Split a document at a page, or merge it with another document of the same batch.

The operator sees the pages as thumbnails, selects the page where a new document should
start (split), or picks a neighbouring document to join (merge). The result goes back to
the QC review queue for acceptance (pipeline.doc_edit).
"""
from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                             QListView, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                             QVBoxLayout)

from pipeline.doc_edit import (DocumentEditError, EditResult, merge_documents, page_numbers,
                           split_document, why_not_editable)
from ui.theme import fit_to_screen, mark

THUMB = 150


class SplitMergeDialog(QDialog):
    def __init__(self, db, audit, username: str, document_id: str, parent=None):
        super().__init__(parent)
        self.db, self.audit, self.username, self.document_id = db, audit, username, document_id
        self.result_edit: EditResult | None = None
        self.setWindowTitle("Split or merge document")
        fit_to_screen(self, 860, 560)
        layout = QVBoxLayout(self)
        row = db.get_document(document_id)
        problem = why_not_editable(row)
        self.heading = QLabel(f"Document {document_id[:8]} · pages {row['page_range'] if row else '?'}")
        layout.addWidget(self.heading)
        if problem:
            note = QLabel(f"This document cannot be changed: {problem}.")
            note.setWordWrap(True)
            layout.addWidget(note)

        hint = QLabel("Split: select the page where a NEW document starts.")
        mark(hint, "muted")
        layout.addWidget(hint)
        self.pages_list = QListWidget()
        self.pages_list.setViewMode(QListView.ViewMode.IconMode)
        self.pages_list.setIconSize(QSize(THUMB, THUMB))
        self.pages_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.pages_list.setMovement(QListView.Movement.Static)
        self.pages_list.setMinimumHeight(220)
        self._fill_pages(row)
        self.pages_list.itemSelectionChanged.connect(self._sync)
        layout.addWidget(self.pages_list, stretch=1)
        self.split_btn = QPushButton("Start a new document at the selected page")
        mark(self.split_btn, "primary")
        self.split_btn.clicked.connect(self.do_split)
        layout.addWidget(self.split_btn)

        merge_row = QHBoxLayout()
        merge_row.addWidget(QLabel("Merge with:"))
        self.merge_combo = QComboBox()
        merge_row.addWidget(self.merge_combo, stretch=1)
        self.merge_btn = QPushButton("Merge")
        self.merge_btn.clicked.connect(self.do_merge)
        merge_row.addWidget(self.merge_btn)
        layout.addLayout(merge_row)
        self._fill_merge_choices(row)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._editable = not problem
        self._sync()

    # -- content ----------------------------------------------------------------------------
    def _fill_pages(self, row) -> None:
        try:
            pages = json.loads(row["qc_detail"] or "{}").get("pages", {}) if row else {}
        except ValueError:
            pages = {}
        for n in (page_numbers(row) if row else []):
            info = pages.get(str(n)) or {}
            item = QListWidgetItem(f"Page {n}")
            item.setData(Qt.ItemDataRole.UserRole, n)
            path = info.get("working_path") or info.get("original_path")
            if path and Path(path).exists():
                pix = QPixmap(str(path))
                if not pix.isNull():
                    item.setIcon(QIcon(pix.scaled(THUMB, THUMB, Qt.AspectRatioMode.KeepAspectRatio,
                                                  Qt.TransformationMode.SmoothTransformation)))
            self.pages_list.addItem(item)

    def _fill_merge_choices(self, row) -> None:
        self.merge_combo.clear()
        if row is None:
            return
        for other in self.db.list_documents(batch_id=row["batch_id"]):
            if other["document_id"] == row["document_id"] or why_not_editable(other):
                continue
            self.merge_combo.addItem(f"{other['document_id'][:8]} · pages {other['page_range']}",
                                     other["document_id"])

    def selected_page(self) -> int | None:
        items = self.pages_list.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None

    def _sync(self) -> None:
        page = self.selected_page()
        first = self.pages_list.item(0).data(Qt.ItemDataRole.UserRole) if self.pages_list.count() else None
        self.split_btn.setEnabled(getattr(self, "_editable", False) and page is not None and page != first)
        self.merge_btn.setEnabled(getattr(self, "_editable", False) and self.merge_combo.count() > 0)

    # -- actions ----------------------------------------------------------------------------
    def _finish(self, result: EditResult, what: str) -> None:
        self.result_edit = result
        note = ""
        if result.already_delivered:
            note = (" The earlier PDF had already been delivered; it stays as sent. The new "
                    "document(s) are delivered when you accept them in QC Review.")
        QMessageBox.information(self, "Done", f"{what}. The new document(s) are in the QC review "
                                              f"queue for acceptance.{note}")
        self.accept()

    def do_split(self) -> None:
        page = self.selected_page()
        if page is None:
            return
        try:
            result = split_document(self.db, self.audit, self.username, self.document_id, page)
        except DocumentEditError as e:
            self.message.setText(str(e))
            return
        self._finish(result, f"Split before page {page}")

    def do_merge(self) -> None:
        other = self.merge_combo.currentData()
        if not other:
            return
        try:
            result = merge_documents(self.db, self.audit, self.username, self.document_id, other)
        except DocumentEditError as e:
            self.message.setText(str(e))
            return
        self._finish(result, "Merged")
