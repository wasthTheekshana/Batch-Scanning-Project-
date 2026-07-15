"""Main window shell — tab bar with role-gated panels (HLD §16, §10.1)."""
from __future__ import annotations

from PyQt6.QtWidgets import (QMainWindow, QPushButton, QTabWidget,
                             QVBoxLayout, QWidget)

from core.rbac import Session
from ui.admin_panel import AdminPanel
from ui.image_viewer import ImageViewer
from ui.index_editor import IndexEditor
from ui.job_monitor import JobMonitor
from ui.qc_review import QCReviewPanel
from ui.scan_wizard import ScanWizard
from ui.workflow_designer import WorkflowDesigner


class MainWindow(QMainWindow):
    def __init__(self, db, profile_manager, bus, pipeline=None, audit=None,
                 staging_dir="staging", backup_service=None,
                 cddvd_channel=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.profile_manager = profile_manager
        self.bus = bus
        self.pipeline = pipeline
        self.audit = audit
        self.staging_dir = staging_dir
        user = Session.current_user()

        self.setWindowTitle("DOK Scanner — Batch Document Capture")
        self.resize(1200, 800)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # Scan tab
        scan_tab = QWidget()
        scan_layout = QVBoxLayout(scan_tab)
        self.new_batch_button = QPushButton("New scan batch…")
        self.new_batch_button.setEnabled(
            user is not None and user.can("scan.run"))
        self.new_batch_button.clicked.connect(self.open_scan_wizard)
        scan_layout.addWidget(self.new_batch_button)
        self.viewer = ImageViewer()
        scan_layout.addWidget(self.viewer, stretch=1)
        self.tabs.addTab(scan_tab, "Scan")

        # QC review tab
        self.qc_panel = QCReviewPanel(db, audit, user, pipeline=pipeline)
        self.tabs.addTab(self.qc_panel, "QC Review")
        # always show current queue when the operator opens the tab
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # Index tab (zones from the first active project, if any)
        zones = []
        projects = [dict(p) for p in db.list_projects("active")]
        if projects:
            import json
            cfg = json.loads(projects[0]["config_json"])
            zones = cfg.get("ocr", {}).get("zones", [])
        self.index_editor = IndexEditor(db, zones, audit, user)
        self.tabs.addTab(self.index_editor, "Index")

        # Job monitor tab
        self.job_monitor = JobMonitor(bus)
        self.tabs.addTab(self.job_monitor, "Job Monitor")

        # Workflow designer — project.edit only (HLD §17.5 role gate)
        if user is not None and user.can("project.edit"):
            self.designer = WorkflowDesigner(db, profile_manager, user)
            self.tabs.addTab(self.designer, "Projects")

        # Admin panel — user.manage only (Phase 2)
        if user is not None and user.can("user.manage"):
            self.admin_panel = AdminPanel(db, user,
                                          backup_service=backup_service,
                                          cddvd_channel=cddvd_channel)
            self.tabs.addTab(self.admin_panel, "Admin")

        bus.qc_ready.connect(lambda _doc_id: self.qc_panel.refresh())
        self.statusBar().showMessage(
            f"Logged in as {user.username} ({user.role})" if user else
            "Not logged in")

    def _on_tab_changed(self, index: int) -> None:
        if self.tabs.widget(index) is self.qc_panel:
            self.qc_panel.refresh()

    def open_scan_wizard(self) -> None:
        projects = []
        import json
        for row in self.db.list_projects("active"):
            cfg = json.loads(row["config_json"])
            projects.append({"project_id": row["project_id"],
                             "name": row["name"], "config": cfg})
        wizard = ScanWizard(projects, self.staging_dir, self)
        wizard.batch_ready.connect(self.start_batch)
        wizard.exec()

    def start_batch(self, batch_id: str, jobs: list) -> None:
        user = Session.current_user()
        if self.pipeline is not None:
            self.pipeline.submit_batch(
                batch_id, jobs, operator=user.username if user else "unknown")
        if self.audit:
            self.audit.log("scan", user.username if user else "unknown",
                           batch_id=batch_id, pages=len(jobs))
        self.viewer.load_pages([j.image_path for j in jobs])
