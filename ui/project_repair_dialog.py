"""Repair projects whose stored settings cannot be read (see core.project_repair)."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QMessageBox, QPushButton,
                             QVBoxLayout)

from core.project_repair import describe, repair, unreadable_rows


class ProjectRepairDialog(QDialog):
    repaired = pyqtSignal(str)            # project_id

    def __init__(self, db, profile_manager, audit=None, user=None, parent=None):
        super().__init__(parent)
        self.db, self.pm, self.audit, self.user = db, profile_manager, audit, user
        self.setWindowTitle("Repair projects")
        self.resize(640, 360)
        layout = QVBoxLayout(self)
        intro = QLabel("These projects' settings are damaged, so they are not offered "
                       "for scanning. Repair puts back the newest intact copy; nothing "
                       "is deleted.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.list = QListWidget()
        layout.addWidget(self.list)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.repair_btn = QPushButton("Repair selected")
        self.repair_btn.clicked.connect(self.repair_selected)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addStretch(1)
        row.addWidget(self.repair_btn)
        row.addWidget(close)
        layout.addLayout(row)
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        for r in unreadable_rows(self.db):
            item = QListWidgetItem(f"{r['name']}: {describe(self.pm, r['project_id'])}")
            item.setData(Qt.ItemDataRole.UserRole, r["project_id"])
            self.list.addItem(item)
        self.repair_btn.setEnabled(self.list.count() > 0)
        if not self.list.count():
            self.status.setText("No damaged projects.")
        elif self.list.currentRow() < 0:
            self.list.setCurrentRow(0)

    def _confirm_default(self, name: str) -> bool:
        return QMessageBox.question(
            self, "No intact copy",
            f"There is no intact copy of '{name}'. It can only be reset to the default "
            "settings (scanner, quality checks, export and indexing settings are lost; "
            "documents already scanned are not affected).\n\nReset it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def repair_selected(self) -> dict | None:
        item = self.list.currentItem()
        if item is None:
            return None
        project_id = item.data(Qt.ItemDataRole.UserRole)
        row = self.db.get_project(project_id)
        try:
            result = repair(self.pm, self.db, project_id)
        except ValueError:                           # no intact copy
            if not self._confirm_default(row["name"]):
                return None
            result = repair(self.pm, self.db, project_id, allow_default=True)
        except Exception as e:
            self.status.setText(f"Could not repair this project ({type(e).__name__}).")
            return None
        if self.audit is not None:
            self.audit.log("project_repaired",
                           self.user.username if self.user else "unknown",
                           project_id=project_id, source=result["source"])
        self.repaired.emit(project_id)
        self.refresh()
        self.status.setText(f"'{result['name']}' was restored from {result['source']}.")
        return result
