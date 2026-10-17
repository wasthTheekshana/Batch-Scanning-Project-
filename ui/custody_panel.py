"""Custody tab: create bundles, print their QR label sheet, record
checkpoints, flag incidents, and see the reconciliation view
(DOK_Scanner_Enhancement_Plan §3.2)."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QInputDialog, QFileDialog, QFormLayout, QGridLayout, QSpinBox,
                             QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QMessageBox,
                             QPushButton, QSplitter, QTabWidget, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from custody.bundle_manager import (MAX_LABEL_COPIES, VALID_STAGES, bundle_summary, create_bundle,
                                    flag_incident, generate_label_sheet,
                                    reconciliation_summary, record_checkpoint)
from custody.timeline import bundle_timeline, export_timeline
from ui.theme import mark, scroll_column, tidy_list

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
        tidy_list(self.bundle_list, 240)
        self.bundle_list.currentItemChanged.connect(self._on_select)
        ll.addWidget(self.bundle_list)

        form = QFormLayout()
        self.new_bundle_label = QLineEdit()
        form.addRow("New bundle label", self.new_bundle_label)
        self.new_expected = QSpinBox()
        self.new_expected.setRange(0, 100000)
        self.new_expected.setSpecialValueText("(not set)")
        self.new_expected.setToolTip(
            "How many documents this bundle should hold. Once it has been scanned, "
            "a different count is flagged in the reconciliation.")
        form.addRow("Documents expected", self.new_expected)
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

        actions = QGridLayout()
        actions.setHorizontalSpacing(8)
        actions.setVerticalSpacing(6)
        self.print_label_btn = QPushButton("Print label sheet…")
        self.print_label_btn.clicked.connect(self.print_label_sheet)
        self.stage_combo = QComboBox()
        self.stage_combo.addItems(_CHECKPOINT_STAGES)
        self.record_checkpoint_btn = QPushButton("Record checkpoint")
        self.record_checkpoint_btn.clicked.connect(self.record_checkpoint)
        self.incident_combo = QComboBox()
        self.incident_combo.addItems(_INCIDENT_STAGES)
        self.flag_incident_btn = QPushButton("Flag incident")
        self.flag_incident_btn.clicked.connect(self.flag_incident)
        self.expected_spin = QSpinBox()
        self.expected_spin.setRange(0, 100000)
        self.expected_spin.setSpecialValueText("(not set)")
        self.set_expected_btn = QPushButton("Set")
        self.set_expected_btn.clicked.connect(self.set_expected)
        actions.addWidget(self.print_label_btn, 0, 0, 1, 2)
        actions.addWidget(self.stage_combo, 1, 0)
        actions.addWidget(self.record_checkpoint_btn, 1, 1)
        actions.addWidget(self.incident_combo, 2, 0)
        actions.addWidget(self.flag_incident_btn, 2, 1)
        expected_row = QHBoxLayout()
        expected_row.addWidget(QLabel("Expected documents"))
        expected_row.addWidget(self.expected_spin, stretch=1)
        expected_row.addWidget(self.set_expected_btn)
        actions.addLayout(expected_row, 3, 0, 1, 2)
        actions.setColumnStretch(0, 1)
        actions.setColumnStretch(1, 1)
        rl.addLayout(actions)
        self.link_earlier_btn = QPushButton("Link earlier documents to their bundles")
        self.link_earlier_btn.setToolTip(
            "Read the saved page images of documents scanned before bundles tracked "
            "their documents, and link each to the bundle whose QR label it carries.")
        self.link_earlier_btn.clicked.connect(self.link_earlier)
        rl.addWidget(self.link_earlier_btn)

        # the three tables get a page each: stacked in one column they were squeezed to
        # a couple of rows apiece
        self.views = QTabWidget()
        self.views.setDocumentMode(True)
        self.recon_table = QTableWidget(0, 10)
        self.recon_table.setHorizontalHeaderLabels(
            ["Bundle", "Status", "Received", "Scanned", "QC", "Storage",
             "Returned", "Documents", "Expected", "Mismatch"])
        self.recon_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        # ten narrow columns: size each to its heading so none is clipped, and
        # let the last one take the slack
        self.recon_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.recon_table.horizontalHeader().setStretchLastSection(True)
        recon_page = QWidget()
        recon_layout = QVBoxLayout(recon_page)
        recon_layout.setContentsMargins(0, 8, 0, 0)
        recon_layout.addWidget(self.recon_table, stretch=1)
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        recon_layout.addWidget(self.summary_label)
        self.views.addTab(recon_page, "Reconciliation")

        self.docs_table = QTableWidget(0, 6)
        self.docs_table.setHorizontalHeaderLabels(
            ["Document", "Batch", "Pages", "QC", "Delivery", "Scanned (UTC)"])
        self.docs_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.docs_table.horizontalHeader().setStretchLastSection(True)
        self.docs_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        docs_page = QWidget()
        docs_layout = QVBoxLayout(docs_page)
        docs_layout.setContentsMargins(0, 8, 0, 0)
        docs_layout.addWidget(QLabel("Documents scanned with this bundle's QR label"))
        docs_layout.addWidget(self.docs_table, stretch=1)
        self.views.addTab(docs_page, "Documents")

        self.timeline_table = QTableWidget(0, 6)
        self.timeline_table.setHorizontalHeaderLabels(
            ["Time (UTC)", "Source", "Action", "User", "Batch", "Document"])
        self.timeline_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.timeline_table.horizontalHeader().setStretchLastSection(True)
        self.timeline_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        timeline_page = QWidget()
        timeline_layout = QVBoxLayout(timeline_page)
        timeline_layout.setContentsMargins(0, 8, 0, 0)
        timeline_layout.addWidget(self.timeline_table, stretch=1)
        self.export_timeline_btn = QPushButton("Export timeline (CSV/PDF)…")
        # exports leave the workstation: same permission as the Reports tab
        self._can_export = current_user is not None and \
            current_user.can("report.export")
        self.export_timeline_btn.setEnabled(self._can_export)
        self.export_timeline_btn.clicked.connect(self.export_timeline)
        timeline_layout.addWidget(self.export_timeline_btn)
        self.views.addTab(timeline_page, "Timeline")
        rl.addWidget(self.views, stretch=1)
        for table in (self.recon_table, self.docs_table, self.timeline_table):
            table.setMinimumHeight(160)
        # on a short screen the actions and tables scroll instead of being squeezed
        splitter.addWidget(scroll_column(right, 460))
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 620])

        self._sync_buttons()
        self.refresh()

    def _sync_buttons(self) -> None:
        """What acts on a bundle is available only once one is selected."""
        has = self._selected_bundle_id() is not None
        for w in (self.print_label_btn, self.record_checkpoint_btn, self.flag_incident_btn,
                  self.set_expected_btn, self.expected_spin, self.stage_combo,
                  self.incident_combo):
            w.setEnabled(has)
        self.export_timeline_btn.setEnabled(has and self._can_export)

    # -- data -----------------------------------------------------------------
    def refresh(self) -> None:
        keep = self._selected_bundle_id()       # the list is rebuilt: keep the selection
        self.bundle_list.clear()
        if self.project_id is None:
            self.recon_table.setRowCount(0)
            self.hint.setText(
                "No active project — create one first.")
            return
        self.hint.setText("")
        # every bundle, whichever project created it: a label is read from any batch
        for row in self.db.list_bundles():
            item = QListWidgetItem(f"{row['label']} · {row['status']}")
            item.setData(Qt.ItemDataRole.UserRole, row["bundle_id"])
            self.bundle_list.addItem(item)
        self._refresh_reconciliation()
        if keep:
            self._reselect(keep)

    def _refresh_reconciliation(self) -> None:
        rows = reconciliation_summary(self.db)
        self.recon_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            values = [row["label"], row["status"], str(row["received"]),
                     str(row["scanned"]), str(row["qc"]), str(row["storage"]),
                     str(row["returned"]), str(row["documents"]),
                     str(row["expected"] or ""),
                     "Yes" if row["mismatch"] else "No"]
            for c, value in enumerate(values):
                self.recon_table.setItem(r, c, QTableWidgetItem(value))

    def _on_select(self, current, _previous=None) -> None:
        self._sync_buttons()
        if current is None:
            self.selected_label.setText("Select a bundle from the list.")
            self.timeline_table.setRowCount(0)
            self.docs_table.setRowCount(0)
            self.summary_label.setText("")
            return
        bundle_id = current.data(Qt.ItemDataRole.UserRole)
        bundle = self.db.get_bundle(bundle_id)
        self.selected_label.setText(
            f"{bundle['label']} · status: {bundle['status']}")
        self.expected_spin.setValue(bundle["expected_documents"] or 0)
        self._refresh_documents(bundle_id)
        self._refresh_timeline(bundle_id)

    def _refresh_documents(self, bundle_id: str) -> None:
        """The documents scanned under this bundle's QR label, with where each
        one stands, and a one-line summary of the whole bundle."""
        docs = self.db.list_bundle_documents(bundle_id)
        self.docs_table.setRowCount(len(docs))
        for r, d in enumerate(docs):
            values = [d["document_id"][:8], d["batch_id"][:8], d["page_range"] or "",
                      d["validation_status"], d["delivery_status"] or "",
                      d["created_at"][:19].replace("T", " ")]
            for c, value in enumerate(values):
                self.docs_table.setItem(r, c, QTableWidgetItem(str(value)))
        s = bundle_summary(self.db, bundle_id)
        if not s["documents"]:
            expected = s.get("expected")
            self.summary_label.setText(
                "No documents have been scanned with this bundle's label yet."
                + (f" {expected} expected." if expected else ""))
            return
        text = (f"{s['documents']} document(s) under this bundle: {s['approved']} approved, "
                f"{s['corrected']} corrected, {s['rejected']} rejected, "
                f"{s['waiting_qc']} waiting for QC review. {s['delivered']} delivered.")
        if s.get("expected"):
            if s["missing"]:
                text += f" EXPECTED {s['expected']}: {s['missing']} not scanned yet."
            elif s["extra"]:
                text += f" EXPECTED {s['expected']}: {s['extra']} more than expected."
            else:
                text += f" All {s['expected']} expected documents are accounted for."
        self.summary_label.setText(text)

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
        create_bundle(self.db, self.project_id, label, audit=self.audit,
                      expected_documents=self.new_expected.value() or None)
        self.new_bundle_label.clear()
        self.new_expected.setValue(0)
        self.refresh()

    def set_expected(self) -> None:
        bundle_id = self._selected_bundle_id()
        if bundle_id is None:
            return
        self.db.set_bundle_expected(bundle_id, self.expected_spin.value() or None)
        if self.audit:
            self.audit.log("custody_expected_set", self.current_user.username,
                           bundle_id=bundle_id, expected=self.expected_spin.value())
        self.refresh()
        self._reselect(bundle_id)

    def link_earlier(self) -> dict:
        """Link documents scanned before bundles tracked their documents."""
        from custody.backfill import link_earlier_documents
        user = self.current_user.username if self.current_user else "system"
        result = link_earlier_documents(self.db, user, audit=self.audit)
        if self.audit:
            self.audit.log("custody_backfill", user, **result)
        QMessageBox.information(
            self, "Earlier documents",
            f"{result['documents']} document(s) checked: {result['linked']} linked to a "
            f"bundle, {result['no_label']} carried no custody label, "
            f"{result['no_images']} had no saved page images.")
        keep = self._selected_bundle_id()
        self.refresh()
        if keep:
            self._reselect(keep)
        return result

    def print_label_sheet(self) -> None:
        bundle_id = self._selected_bundle_id()
        if bundle_id is None:
            return
        bundle = dict(self.db.get_bundle(bundle_id))
        copies, ok = QInputDialog.getInt(
            self, "Label sheet", "How many QR codes on the page?",
            1, 1, MAX_LABEL_COPIES)
        if not ok:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save label sheet", f"{bundle['label']}_label.pdf",
            "PDF (*.pdf)")
        if not path:
            return
        generate_label_sheet(bundle, path, copies=copies)
        QMessageBox.information(self, "Label sheet",
                                f"Saved to {path} ({copies} QR code(s) on one "
                                "page). Print it and place it with this bundle.")

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
