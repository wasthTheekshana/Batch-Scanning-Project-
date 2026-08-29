"""Custody tab: create bundles, print their QR label sheet, record
checkpoints, flag incidents, and see the reconciliation view
(DOK_Scanner_Enhancement_Plan §3.2)."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QFormLayout,
                             QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QMessageBox,
                             QPushButton, QSplitter, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from custody.bundle_manager import (VALID_STAGES, create_bundle,
                                    flag_incident, generate_label_sheet,
                                    reconciliation_summary, record_checkpoint)
from ui.theme import mark

_CHECKPOINT_STAGES = ["prep", "scanning", "qc", "storage", "return"]
_INCIDENT_STAGES = ["damaged", "missing", "compromised"]


class CustodyPanel(QWidget):
    def __init__(self, db, audit, current_user, parent=None):
        super().__init__(parent)
        self.db = db
        self.audit = audit
        self.current_user = current_user
        self.project_id = "proj-1"  # first/only active project by default;
        # a multi-project selector is out of scope for this MVP panel

        layout = QHBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # -- left: bundle list + new-bundle form --------------------------------
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(QLabel("Bundles"))
        self.bundle_list = QListWidget()
        self.bundle_list.currentItemChanged.connect(self._on_select)
        ll.addWidget(self.bundle_list)

        form = QFormLayout()
        self.new_bundle_label = QLineEdit()
        form.addRow("New bundle label", self.new_bundle_label)
        ll.addLayout(form)
        self.create_bundle_btn = QPushButton("Create bundle")
        mark(self.create_bundle_btn, "primary")
        self.create_bundle_btn.clicked.connect(self.create_bundle)
        ll.addWidget(self.create_bundle_btn)
        splitter.addWidget(left)

        # -- right: actions + reconciliation table ------------------------------
        right = QWidget()
        rl = QVBoxLayout(right)
        self.selected_label = QLabel("Select a bundle from the list.")
        mark(self.selected_label, "muted")
        rl.addWidget(self.selected_label)

        self.print_label_btn = QPushButton("Print label sheet…")
        self.print_label_btn.clicked.connect(self.print_label_sheet)
        rl.addWidget(self.print_label_btn)

        checkpoint_row = QHBoxLayout()
        self.stage_combo = QComboBox()
        self.stage_combo.addItems(_CHECKPOINT_STAGES)
        checkpoint_row.addWidget(self.stage_combo)
        self.record_checkpoint_btn = QPushButton("Record checkpoint")
        self.record_checkpoint_btn.clicked.connect(self.record_checkpoint)
        checkpoint_row.addWidget(self.record_checkpoint_btn)
        rl.addLayout(checkpoint_row)

        incident_row = QHBoxLayout()
        self.incident_combo = QComboBox()
        self.incident_combo.addItems(_INCIDENT_STAGES)
        incident_row.addWidget(self.incident_combo)
        self.flag_incident_btn = QPushButton("Flag incident")
        self.flag_incident_btn.clicked.connect(self.flag_incident)
        incident_row.addWidget(self.flag_incident_btn)
        rl.addLayout(incident_row)

        rl.addWidget(QLabel("Reconciliation"))
        self.recon_table = QTableWidget(0, 7)
        self.recon_table.setHorizontalHeaderLabels(
            ["Bundle", "Status", "Received", "Scanned", "QC", "Storage",
             "Mismatch"])
        self.recon_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        rl.addWidget(self.recon_table, stretch=1)
        splitter.addWidget(right)
        splitter.setSizes([280, 620])

        self.refresh()

    # -- data -----------------------------------------------------------------
    def refresh(self) -> None:
        self.bundle_list.clear()
        for row in self.db.list_bundles(self.project_id):
            item = QListWidgetItem(f"{row['label']} · {row['status']}")
            item.setData(Qt.ItemDataRole.UserRole, row["bundle_id"])
            self.bundle_list.addItem(item)
        self._refresh_reconciliation()

    def _refresh_reconciliation(self) -> None:
        rows = reconciliation_summary(self.db, self.project_id)
        self.recon_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            values = [row["label"], row["status"], str(row["received"]),
                     str(row["scanned"]), str(row["qc"]), str(row["storage"]),
                     "Yes" if row["mismatch"] else "No"]
            for c, value in enumerate(values):
                self.recon_table.setItem(r, c, QTableWidgetItem(value))

    def _on_select(self, current, _previous=None) -> None:
        if current is None:
            self.selected_label.setText("Select a bundle from the list.")
            return
        bundle_id = current.data(Qt.ItemDataRole.UserRole)
        bundle = self.db.get_bundle(bundle_id)
        self.selected_label.setText(
            f"{bundle['label']} · status: {bundle['status']}")

    def _selected_bundle_id(self) -> str | None:
        item = self.bundle_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    # -- actions --------------------------------------------------------------
    def create_bundle(self) -> None:
        label = self.new_bundle_label.text().strip()
        if not label:
            return
        create_bundle(self.db, self.project_id, label, audit=self.audit)
        self.new_bundle_label.clear()
        self.refresh()

    def print_label_sheet(self) -> None:
        bundle_id = self._selected_bundle_id()
        if bundle_id is None:
            return
        bundle = dict(self.db.get_bundle(bundle_id))
        path, _ = QFileDialog.getSaveFileName(
            self, "Save label sheet", f"{bundle['label']}_label.pdf",
            "PDF (*.pdf)")
        if not path:
            return
        generate_label_sheet(bundle, path)
        QMessageBox.information(self, "Label sheet",
                                f"Saved to {path}. Print it and place it "
                                "as the first page of this bundle.")

    def record_checkpoint(self) -> None:
        bundle_id = self._selected_bundle_id()
        if bundle_id is None:
            return
        stage = self.stage_combo.currentText()
        record_checkpoint(self.db, bundle_id, stage,
                          self.current_user.username, audit=self.audit)
        self.refresh()

    def flag_incident(self) -> None:
        bundle_id = self._selected_bundle_id()
        if bundle_id is None:
            return
        kind = self.incident_combo.currentText()
        flag_incident(self.db, bundle_id, kind, self.current_user.username,
                      audit=self.audit)
        self.refresh()
