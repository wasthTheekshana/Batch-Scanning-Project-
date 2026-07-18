"""Reports tab (HLD §10.1): batch/QC statistics for report.view roles,
QC report PDF export for report.export roles."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from export.qc_report import batch_statistics, write_qc_report


class ReportsPanel(QWidget):
    def __init__(self, db, current_user, parent=None):
        super().__init__(parent)
        self.db = db
        self.user = current_user
        layout = QHBoxLayout(self)

        left = QVBoxLayout()
        left.addWidget(QLabel("Batches"))
        self.batch_list = QListWidget()
        self.batch_list.currentItemChanged.connect(self._show_batch)
        left.addWidget(self.batch_list)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        left.addWidget(refresh)
        layout.addLayout(left, stretch=1)

        right = QVBoxLayout()
        self.summary = QLabel("Select a batch.")
        self.summary.setWordWrap(True)
        right.addWidget(self.summary)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Document", "Pages", "Score", "Status", "Delivery", "Duplicate"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        right.addWidget(self.table, stretch=1)

        self.export_btn = QPushButton("Export QC report PDF…")
        from ui.theme import mark
        mark(self.export_btn, "primary")
        can_export = current_user is not None and \
            current_user.can("report.export")
        self.export_btn.setEnabled(can_export)
        self.export_btn.clicked.connect(self.export_pdf)
        right.addWidget(self.export_btn)
        layout.addLayout(right, stretch=3)

        self.refresh()

    def refresh(self) -> None:
        self.batch_list.clear()
        rows = self.db.conn.execute(
            "SELECT * FROM batches ORDER BY created_at DESC").fetchall()
        for row in rows:
            item = QListWidgetItem(
                f"{row['batch_id'][:8]} · {row['scan_date'][:10]} · "
                f"{row['status']} · {row['page_count']} pages")
            item.setData(Qt.ItemDataRole.UserRole, row["batch_id"])
            self.batch_list.addItem(item)
        if self.batch_list.count():
            self.batch_list.setCurrentRow(0)

    def current_batch_id(self) -> str | None:
        item = self.batch_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _show_batch(self, current, _previous=None) -> None:
        if current is None:
            return
        batch_id = current.data(Qt.ItemDataRole.UserRole)
        stats = batch_statistics(self.db, batch_id)
        self.summary.setText(
            f"Batch {batch_id[:8]} — {stats['documents']} documents · "
            f"avg score {stats['avg_score']} · min {stats['min_score']} · "
            f"{stats['delivered']} delivered · "
            f"{stats['duplicates']} duplicates · "
            + ", ".join(f"{k}: {v}" for k, v in
                        sorted(stats["by_status"].items())))
        from PyQt6.QtGui import QBrush, QColor

        from ui.theme import STATUS_COLORS
        self.table.setAlternatingRowColors(True)
        self.table.setRowCount(len(stats["docs"]))
        for i, d in enumerate(stats["docs"]):
            cells = [d["document_id"][:8], d["page_range"],
                     str(d["qc_score"]), d["validation_status"],
                     d["delivery_status"],
                     (d["duplicate_of"] or "")[:8]]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col in (3, 4):
                    color = STATUS_COLORS.get(text)
                    if color:
                        item.setForeground(QBrush(QColor(color)))
                self.table.setItem(i, col, item)

    def export_pdf(self) -> None:
        batch_id = self.current_batch_id()
        if batch_id is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export QC report", f"qc_report_{batch_id[:8]}.pdf",
            "PDF files (*.pdf)")
        if not path:
            return
        out = write_qc_report(self.db, batch_id, path)
        self.db.append_audit(None,
                             self.user.username if self.user else "unknown",
                             "report_export", {"batch_id": batch_id,
                                               "path": str(out)})
        self.summary.setText(f"Report written: {out}")
