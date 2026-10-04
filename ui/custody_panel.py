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
from custody.timeline import bundle_timeline, export_timeline
from ui.theme import mark

_CHECKPOINT_STAGES = ["prep", "scanning", "qc", "storage", "return"]
_INCIDENT_STAGES = ["damaged", "missing", "compromised"]
assert set(_CHECKPOINT_STAGES) | set(_INCIDENT_STAGES) | {"receipt"} == \
    VALID_STAGES, "custody_panel's stage lists drifted from VALID_STAGES"


class CustodyPanel(QWidget):
    def __init__(self, db, audit, current_user, parent=None):
        super().__init__(parent)
        self.db = db
        self.audit = audit
        self.current_user = current_user
        # first/only active project by default; a multi-project selector
        # is out of scope for this MVP panel
        projects = db.list_projects("active")
        self.project_id = projects[0]["project_id"] if projects else None

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

        self.hint = QLabel("")
        mark(self.hint, "muted")
        rl.addWidget(self.hint)

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
        self.recon_table = QTableWidget(0, 8)
        self.recon_table.setHorizontalHeaderLabels(
            ["Bundle", "Status", "Received", "Scanned", "QC", "Storage",
             "Returned", "Mismatch"])
        self.recon_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        rl.addWidget(self.recon_table, stretch=1)

        rl.addWidget(QLabel("Timeline (custody + processing)"))
        self.timeline_table = QTableWidget(0, 6)
        self.timeline_table.setHorizontalHeaderLabels(
            ["Time (UTC)", "Source", "Action", "User", "Batch", "Document"])
        self.timeline_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.timeline_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        rl.addWidget(self.timeline_table, stretch=1)
        self.export_timeline_btn = QPushButton("Export timeline (CSV/PDF)…")
        # exports leave the workstation: same permission as the Reports tab
        self._can_export = current_user is not None and \
            current_user.can("report.export")
        self.export_timeline_btn.setEnabled(self._can_export)
        self.export_timeline_btn.clicked.connect(self.export_timeline)
        rl.addWidget(self.export_timeline_btn)
        splitter.addWidget(right)
        splitter.setSizes([280, 620])

        self.refresh()

    # -- data -----------------------------------------------------------------
    def refresh(self) -> None:
        self.bundle_list.clear()
        if self.project_id is None:
            self.recon_table.setRowCount(0)
            self.hint.setText(
                "No active project — create one first.")
            return
        self.hint.setText("")
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
                     str(row["returned"]),
                     "Yes" if row["mismatch"] else "No"]
            for c, value in enumerate(values):
                self.recon_table.setItem(r, c, QTableWidgetItem(value))

    def _on_select(self, current, _previous=None) -> None:
        if current is None:
            self.selected_label.setText("Select a bundle from the list.")
            self.timeline_table.setRowCount(0)
            return
        bundle_id = current.data(Qt.ItemDataRole.UserRole)
        bundle = self.db.get_bundle(bundle_id)
        self.selected_label.setText(
            f"{bundle['label']} · status: {bundle['status']}")
        self._refresh_timeline(bundle_id)

    def _refresh_timeline(self, bundle_id: str) -> None:
        entries = bundle_timeline(self.db, bundle_id)
        self.timeline_table.setRowCount(len(entries))
        for r, e in enumerate(entries):
            values = [e["timestamp"][:19].replace("T", " "), e["source"],
                      e["action"], e["user"] or "",
                      (e["batch_id"] or "")[:8],
                      (e["document_id"] or "")[:8]]
            for c, value in enumerate(values):
                self.timeline_table.setItem(r, c, QTableWidgetItem(value))

    def _reselect(self, bundle_id: str) -> None:
        """refresh() rebuilds the list; keep the same bundle selected so
        its timeline stays visible after an action."""
        for i in range(self.bundle_list.count()):
            if self.bundle_list.item(i).data(
                    Qt.ItemDataRole.UserRole) == bundle_id:
                self.bundle_list.setCurrentRow(i)
                return

    def _selected_bundle_id(self) -> str | None:
        item = self.bundle_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    # -- actions --------------------------------------------------------------
    def create_bundle(self) -> None:
        label = self.new_bundle_label.text().strip()
        if not label:
            return
        if self.project_id is None:
            self.hint.setText("No active project — create one first.")
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

    def export_timeline(self) -> None:
        if not self._can_export:
            return
        bundle_id = self._selected_bundle_id()
        if bundle_id is None:
            return
        bundle = self.db.get_bundle(bundle_id)
        path, _ = QFileDialog.getSaveFileName(
            self, "Export audit timeline", f"{bundle['label']}_timeline.pdf",
            "PDF (*.pdf);;CSV (*.csv)")
        if not path:
            return
        out = export_timeline(bundle_timeline(self.db, bundle_id), path,
                              title=f"Bundle {bundle['label']}")
        self.audit.log("timeline_export", self.current_user.username,
                       bundle_id=bundle_id, path=str(out))

    def record_checkpoint(self) -> None:
        bundle_id = self._selected_bundle_id()
        if bundle_id is None:
            return
        stage = self.stage_combo.currentText()
        record_checkpoint(self.db, bundle_id, stage,
                          self.current_user.username, audit=self.audit)
        self.refresh()
        self._reselect(bundle_id)

    def flag_incident(self) -> None:
        bundle_id = self._selected_bundle_id()
        if bundle_id is None:
            return
        kind = self.incident_combo.currentText()
        flag_incident(self.db, bundle_id, kind, self.current_user.username,
                      audit=self.audit)
        self.refresh()
        self._reselect(bundle_id)
