"""Search tab: find a scanned document by what is written in its index fields,
then open its PDF, see its history, or go and correct its fields.

A user who may only see their own batches (the operator role) only finds
documents from batches they scanned."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QDate, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDialog,
                             QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QMessageBox, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from core.search import (SearchQuery, count_documents, known_field_names,
                         search_documents)
from core.search_export import MAX_ROWS, export_csv
from custody.timeline import document_timeline, export_timeline
from ui.flow import FlowLayout
from ui.theme import fit_to_screen, mark

PAGE = 100
VALIDATION = [("Any status", None), ("Approved", "auto_approved"),
              ("Corrected", "corrected"), ("Needs review", "pending_qc"),
              ("Rejected", "rejected")]
DELIVERY = [("Any delivery", None), ("Delivered", "delivered_local"),
            ("Not delivered yet", "pending")]
COLUMNS = ["Scanned", "Customer / barcode", "Document types", "Project",
           "Status", "Delivery", "Pages", "Index fields"]


def _summary(fields: dict, limit: int = 3) -> str:
    shown = [f"{k}: {v}" for k, v in fields.items()
             if k not in ("barcode", "doc_types") and str(v).strip()]
    return "   ".join(shown[:limit]) + ("  ..." if len(shown) > limit else "")


class TimelineDialog(QDialog):
    """One document's history (audit rows), exportable to CSV or PDF."""

    def __init__(self, entries: list[dict], title: str, parent=None, user=None):
        super().__init__(parent)
        self.user = user
        self.setWindowTitle(title)
        fit_to_screen(self, 760, 420)
        self.entries = entries
        layout = QVBoxLayout(self)
        self.table = QTableWidget(len(entries), 4)
        self.table.setHorizontalHeaderLabels(["Time (UTC)", "Source", "Action", "User"])
        for row, e in enumerate(entries):
            for col, key in enumerate(("timestamp", "source", "action", "user")):
                self.table.setItem(row, col, QTableWidgetItem(str(e.get(key, "") or "")))
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        self.export_btn = QPushButton("Export (CSV/PDF)...")
        self.export_btn.clicked.connect(self.export)
        self.export_btn.setVisible(self._may_export())
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addStretch(1)
        row.addWidget(self.export_btn)
        row.addWidget(close)
        layout.addLayout(row)

    def _may_export(self) -> bool:
        return self.user is not None and self.user.can("report.export")

    def export(self) -> None:
        if not self._may_export():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export timeline", "document_timeline.csv",
            "CSV (*.csv);;PDF (*.pdf)")
        if path:
            export_timeline(self.entries, path, title=self.windowTitle())


class SearchPanel(QWidget):
    edit_requested = pyqtSignal(str)          # document_id: open it in the Index tab

    def __init__(self, db, user, parent=None, audit=None):
        super().__init__(parent)
        self.db = db
        self.user = user
        self.audit = audit
        self._rows: list[dict] = []
        self._query = SearchQuery()
        self._total = 0
        self._can_edit = bool(user is not None and (
            user.can("scan.run") or user.can("qc.review")))
        # who may see everything; others only the batches they scanned
        self._own_only = (user.username if user is not None
                          and not user.can("batch.view_all") else None)

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.text = QLineEdit()
        self.text.setPlaceholderText(
            "Customer ID, NIC, name, account number... (every word must match)")
        self.text.returnPressed.connect(self.search)
        self.search_btn = QPushButton("Search")
        mark(self.search_btn, "primary")
        self.search_btn.clicked.connect(self.search)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear)
        top.addWidget(self.text, stretch=1)
        top.addWidget(self.search_btn)
        top.addWidget(self.clear_btn)
        layout.addLayout(top)

        filters = FlowLayout()
        self.project = QComboBox()
        self.status = QComboBox()
        for label, key in VALIDATION:
            self.status.addItem(label, key)
        self.delivery = QComboBox()
        for label, key in DELIVERY:
            self.delivery.addItem(label, key)
        self.use_dates = QCheckBox("Between")
        self.date_from = QDateEdit(QDate.currentDate().addMonths(-1))
        self.date_to = QDateEdit(QDate.currentDate())
        for edit in (self.date_from, self.date_to):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd")
        self.field = QComboBox()
        self.field_value = QLineEdit()
        self.field_value.setPlaceholderText("value")
        for w in (self.project, self.status, self.delivery, self.use_dates,
                  self.date_from, QLabel("and"), self.date_to):
            filters.addWidget(w)
        layout.addLayout(filters)
        by_field = QHBoxLayout()
        by_field.addWidget(QLabel("Field"))
        by_field.addWidget(self.field)
        by_field.addWidget(self.field_value, stretch=1)
        layout.addLayout(by_field)

        self.summary = QLabel("")
        mark(self.summary, "muted")
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemDoubleClicked.connect(lambda _i: self.open_pdf())
        layout.addWidget(self.table, stretch=1)

        actions = FlowLayout()
        self.open_btn = QPushButton("Open PDF")
        self.folder_btn = QPushButton("Show in folder")
        self.timeline_btn = QPushButton("History...")
        self.edit_btn = QPushButton("Correct index fields...")
        self.export_btn = QPushButton("Download results (CSV)...")
        self.export_btn.setToolTip(
            "Save every document this search found, with its index fields, as a "
            "CSV file that opens in Excel.")
        self.export_btn.clicked.connect(self.export_results)
        self.more_btn = QPushButton("Load more")
        self.open_btn.clicked.connect(self.open_pdf)
        self.folder_btn.clicked.connect(self.show_folder)
        self.timeline_btn.clicked.connect(self.show_history)
        self.edit_btn.clicked.connect(self.edit_fields)
        self.more_btn.clicked.connect(self.load_more)
        for b in (self.open_btn, self.folder_btn, self.timeline_btn, self.edit_btn,
                  self.export_btn):
            actions.addWidget(b)
        actions.addWidget(self.more_btn)
        layout.addLayout(actions)
        self.reload_choices()
        self._selection_changed()
        self.more_btn.hide()

    # -- filters -------------------------------------------------------------------
    def reload_choices(self) -> None:
        """Projects and field names, refreshed whenever the tab is shown."""
        current_project, current_field = self.project.currentData(), self.field.currentText()
        self.project.blockSignals(True)
        self.project.clear()
        self.project.addItem("Any project", None)
        for p in self.db.list_projects():
            self.project.addItem(p["name"], p["project_id"])
        index = self.project.findData(current_project)
        self.project.setCurrentIndex(max(0, index))
        self.project.blockSignals(False)
        self.field.clear()
        self.field.addItem("(any field)", None)
        for name in known_field_names(self.db):
            self.field.addItem(name, name)
        index = self.field.findText(current_field)
        self.field.setCurrentIndex(max(0, index))

    def clear(self) -> None:
        self.text.clear()
        self.field_value.clear()
        self.project.setCurrentIndex(0)
        self.status.setCurrentIndex(0)
        self.delivery.setCurrentIndex(0)
        self.use_dates.setChecked(False)
        self.field.setCurrentIndex(0)
        self.search()

    def _build_query(self) -> SearchQuery:
        filters = {}
        if self.field.currentData() and self.field_value.text().strip():
            filters[self.field.currentData()] = self.field_value.text().strip()
        return SearchQuery(
            text=self.text.text(), field_filters=filters,
            project_id=self.project.currentData(),
            validation_status=self.status.currentData(),
            delivery_status=self.delivery.currentData(),
            date_from=(self.date_from.date().toString("yyyy-MM-dd")
                       if self.use_dates.isChecked() else None),
            date_to=(self.date_to.date().toString("yyyy-MM-dd")
                     if self.use_dates.isChecked() else None),
            only_operator=self._own_only, limit=PAGE)

    # -- searching --------------------------------------------------------------------
    def search(self) -> None:
        self._query = self._build_query()
        self._rows = []
        self.table.setRowCount(0)
        self._total = count_documents(self.db, self._query)
        self._append(search_documents(self.db, self._query))

    def load_more(self) -> None:
        self._query.offset = len(self._rows)
        self._append(search_documents(self.db, self._query))

    def _append(self, rows: list[dict]) -> None:
        for row in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            f = row["fields"]
            cells = [row["created_at"][:16].replace("T", " "), f.get("barcode", ""),
                     f.get("doc_types", ""), row["project_name"],
                     row["validation_status"], row["delivery_status"] or "",
                     row["page_range"] or "", _summary(f)]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                item.setToolTip(str(text))
                self.table.setItem(r, col, item)
            self._rows.append(row)
        shown = len(self._rows)
        self.summary.setText(
            "No documents found." if self._total == 0
            else f"{self._total} document(s) found" +
                 (f", showing the newest {shown}." if shown < self._total else "."))
        self.more_btn.setVisible(shown < self._total)
        self.export_btn.setEnabled(self._total > 0 and self._may_export())
        self._selection_changed()

    # -- download ---------------------------------------------------------------------
    def _may_export(self) -> bool:
        return self.user is not None and self.user.can("report.export")

    def export_results(self, path: str | None = None) -> int:
        """Save everything the last search found as CSV. Returns the number of
        documents written (0 when cancelled, refused or empty)."""
        if not self._may_export() or self._total == 0:
            return 0
        if not path:
            path, _ = QFileDialog.getSaveFileName(
                self, "Download search results", "search_results.csv", "CSV (*.csv)")
        if not path:
            return 0
        try:
            count, truncated = export_csv(self.db, self._query, path)
        except OSError:
            QMessageBox.warning(self, "Download failed",
                                "The file could not be written. Choose another folder.")
            return 0
        if self.audit is not None:
            self.audit.log("search_export", self.user.username, documents=count,
                           truncated=truncated)
        note = f"{count} document(s) saved."
        if truncated:
            note += f" Only the first {MAX_ROWS} are included: narrow the search."
        self.summary.setText(note)
        return count

    # -- the selected result -------------------------------------------------------------
    def selected(self) -> dict | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return self._rows[rows[0].row()] if rows else None

    def _selection_changed(self) -> None:
        row = self.selected()
        has = row is not None
        self.open_btn.setEnabled(has and bool(row["storage_path"]))
        self.folder_btn.setEnabled(has and bool(row["storage_path"]))
        self.timeline_btn.setEnabled(has)
        self.edit_btn.setEnabled(has and self._can_edit)

    def open_pdf(self) -> None:
        row = self.selected()
        if row is None or not self._exists(row):
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(row["storage_path"]))

    def show_folder(self) -> None:
        row = self.selected()
        if row is None or not self._exists(row):
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(row["storage_path"]).parent)))

    def _exists(self, row: dict) -> bool:
        if row["storage_path"] and Path(row["storage_path"]).is_file():
            return True
        QMessageBox.warning(
            self, "File not found",
            "The PDF is no longer at the place it was saved. It may have been "
            "moved or deleted; the delivered copy may still exist.")
        return False

    def show_history(self) -> None:
        row = self.selected()
        if row is None:
            return
        TimelineDialog(document_timeline(self.db, row["document_id"]),
                       f"History of {row['document_id'][:8]}", self, user=self.user).exec()

    def edit_fields(self) -> None:
        row = self.selected()
        if row is not None and self._can_edit:
            self.edit_requested.emit(row["document_id"])
