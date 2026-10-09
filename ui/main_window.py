"""Main window shell — brand header + tab navigation with role-gated
panels (HLD §16, §10.1)."""
from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QMainWindow, QPushButton,
                             QTabWidget, QVBoxLayout, QWidget)

from core.rbac import Session
from ui.admin_panel import AdminPanel
from ui.image_viewer import ImageViewer
from ui.index_editor import IndexEditor
from ui.job_monitor import JobMonitor
from ui.qc_review import QCReviewPanel
from ui.scan_wizard import ScanWizard
from ui.theme import ACCENT, icon, mark
from ui.workflow_designer import WorkflowDesigner

ROLE_LABELS = {"admin": "Administrator", "operator": "Operator",
               "qc_officer": "QC Officer", "viewer": "Viewer"}


class MainWindow(QMainWindow):
    def __init__(self, db, profile_manager, bus, pipeline=None, audit=None,
                 staging_dir="staging", backup_service=None,
                 cddvd_channel=None, central_state=None, sync_service=None,
                 sync_worker=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.profile_manager = profile_manager
        self.bus = bus
        self.pipeline = pipeline
        self.audit = audit
        self.staging_dir = staging_dir
        user = Session.current_user()

        self.setWindowTitle("DOK Scanner — Batch Document Capture")
        self.setWindowIcon(icon("fa5s.file-import", color=ACCENT))
        self.resize(1280, 840)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        # -- header band ------------------------------------------------------
        header = QWidget()
        header.setObjectName("HeaderBar")
        header.setFixedHeight(58)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(18, 8, 18, 8)
        brand = QVBoxLayout()
        brand.setSpacing(0)
        title = QLabel("DOK Scanner")
        title.setObjectName("HeaderTitle")
        sub = QLabel("Batch capture · Auto-QC · Delivery")
        sub.setObjectName("HeaderSub")
        brand.addWidget(title)
        brand.addWidget(sub)
        hl.addLayout(brand)
        hl.addStretch(1)
        if user is not None:
            chip = QLabel(f"{user.username}  ·  "
                          f"{ROLE_LABELS.get(user.role, user.role)}")
            chip.setObjectName("UserChip")
            hl.addWidget(chip)
        self.signout_btn = QPushButton("Sign out")
        self.signout_btn.clicked.connect(self.sign_out)
        hl.addWidget(self.signout_btn)
        root.addWidget(header)

        # -- navigation tabs ---------------------------------------------------
        self.tabs = QTabWidget()
        self.tabs.setIconSize(QSize(16, 16))
        self.tabs.setDocumentMode(True)
        root.addWidget(self.tabs, stretch=1)

        # Scan tab
        scan_tab = QWidget()
        scan_layout = QVBoxLayout(scan_tab)
        scan_layout.setContentsMargins(12, 12, 12, 12)
        bar = QHBoxLayout()
        self.new_batch_button = QPushButton("  New scan batch")
        self.new_batch_button.setIcon(icon("fa5s.plus", color="white"))
        mark(self.new_batch_button, "primary")
        self.new_batch_button.setEnabled(
            user is not None and user.can("scan.run"))
        self.new_batch_button.clicked.connect(self.open_scan_wizard)
        bar.addWidget(self.new_batch_button)
        hint = QLabel("Import files, watch a hot folder, or acquire from "
                      "a TWAIN/WIA scanner.")
        mark(hint, "muted")
        bar.addWidget(hint)
        bar.addStretch(1)
        scan_layout.addLayout(bar)
        self.viewer = ImageViewer()
        scan_layout.addWidget(self.viewer, stretch=1)
        self.tabs.addTab(scan_tab, icon("fa5s.file-import"), "Scan")

        # QC review tab
        self.qc_panel = QCReviewPanel(db, audit, user, pipeline=pipeline)
        self.tabs.addTab(self.qc_panel, icon("fa5s.check-double"),
                         "QC Review")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # Index tab (zones from the first active project, if any)
        zones = []
        ocr_mode = "zone"
        projects = [dict(p) for p in db.list_projects("active")]
        if projects:
            import json
            cfg = json.loads(projects[0]["config_json"])
            zones = cfg.get("ocr", {}).get("zones", [])
            ocr_mode = cfg.get("ocr", {}).get("mode", "zone")
        self.index_editor = IndexEditor(
            db, zones, audit, user, full_page=(ocr_mode == "full_page"))
        self.tabs.addTab(self.index_editor, icon("fa5s.tags"), "Index")

        # Job monitor tab
        self.job_monitor = JobMonitor(bus)
        self.tabs.addTab(self.job_monitor, icon("fa5s.tasks"), "Job Monitor")

        # Reports tab — report.view roles (admin, qc_officer, viewer)
        if user is not None and user.can("report.view"):
            from ui.reports_panel import ReportsPanel
            self.reports_panel = ReportsPanel(db, user)
            self.tabs.addTab(self.reports_panel, icon("fa5s.chart-bar"),
                             "Reports")

        # Workflow designer — project.edit only (HLD §17.5 role gate)
        if user is not None and user.can("project.edit"):
            self.designer = WorkflowDesigner(db, profile_manager, user)
            self.tabs.addTab(self.designer, icon("fa5s.project-diagram"),
                             "Projects")

        # Admin panel — user.manage only (Phase 2)
        if user is not None and user.can("user.manage"):
            self.admin_panel = AdminPanel(db, user,
                                          backup_service=backup_service,
                                          cddvd_channel=cddvd_channel,
                                          central_state=central_state,
                                          central_service=sync_service,
                                          central_worker=sync_worker,
                                          bus=bus)
            self.tabs.addTab(self.admin_panel, icon("fa5s.user-shield"),
                             "Admin")

        # Custody tab - custody.manage (admin, operator, qc_officer)
        if user is not None and user.can("custody.manage"):
            from ui.custody_panel import CustodyPanel
            self.custody_panel = CustodyPanel(db, audit, user)
            self.tabs.addTab(self.custody_panel, icon("fa5s.boxes"),
                             "Custody")

        bus.qc_ready.connect(lambda _doc_id: self.qc_panel.refresh())
        self.statusBar().showMessage(
            f"Signed in as {user.username} "
            f"({ROLE_LABELS.get(user.role, user.role)})" if user else
            "Not signed in")

    def _active_zones(self) -> list:
        import json
        for row in self.db.list_projects("active"):
            cfg = json.loads(row["config_json"])
            zones = cfg.get("ocr", {}).get("zones", [])
            if zones:
                return zones
        return []

    def _active_ocr_mode(self) -> str:
        """The first active project's ocr.mode ("zone" or "full_page"),
        mirroring _active_zones()'s "first active project" logic — kept
        as a separate lookup so the two can disagree on which project
        each pulls from without changing _active_zones()'s return type."""
        import json
        for row in self.db.list_projects("active"):
            cfg = json.loads(row["config_json"])
            zones = cfg.get("ocr", {}).get("zones", [])
            if zones:
                return cfg.get("ocr", {}).get("mode", "zone")
        return "zone"

    def _on_tab_changed(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if widget is self.qc_panel:
            self.qc_panel.refresh()
        elif widget is getattr(self, "reports_panel", None):
            self.reports_panel.refresh()
        elif widget is self.index_editor:
            # zones (and mode) may have been edited in the designer since
            # startup
            self.index_editor.set_zones(
                self._active_zones(),
                full_page=(self._active_ocr_mode() == "full_page"))
            self.index_editor.refresh()
        elif widget is getattr(self, "custody_panel", None):
            self.custody_panel.refresh()

    def sign_out(self) -> None:
        Session.logout(self.db)
        self.close()

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
