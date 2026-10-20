"""Main window shell — brand header + tab navigation with role-gated
panels (HLD §16, §10.1)."""
from __future__ import annotations

import logging

from PyQt6.QtCore import QSize, Qt, pyqtSlot, pyqtSignal
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                             QPushButton, QVBoxLayout, QWidget)

from core.project_runtime import ProjectUnavailable
from core.project_store import load_config, readable_projects
from core.rbac import Session
from ocr.doc_types import all_zones
from ui.admin_panel import AdminPanel
from ui.image_viewer import ImageViewer
from ui.index_editor import IndexEditor
from ui.job_monitor import JobMonitor
from ui.qc_review import QCReviewPanel
from ui.scan_wizard import ScanWizard
from ui.sidebar import SideNav
from ui.theme import ACCENT, fit_to_screen, icon, mark, nav_icon
from ui.workflow_designer import WorkflowDesigner

log = logging.getLogger(__name__)

_MAX_REFUSED_LINES = 10

REENROLL_NOTICE = "Central: new enrollment code needed"

ROLE_LABELS = {"admin": "Administrator", "operator": "Operator",
               "qc_officer": "QC Officer", "viewer": "Viewer"}


DEFAULT_W, DEFAULT_H = 1360, 860


class BrandBlock(QWidget):
    """The product name at the top of the sidebar (just the mark when the rail is narrow)."""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(0)
        self.title = QLabel("DOK Scanner")
        self.title.setObjectName("HeaderTitle")
        self.sub = QLabel("Capture · QC · Delivery")
        self.sub.setObjectName("HeaderSub")
        layout.addWidget(self.title)
        layout.addWidget(self.sub)

    def set_compact(self, compact: bool) -> None:
        self.title.setText("DOK" if compact else "DOK Scanner")
        self.sub.setVisible(not compact)


class UserBlock(QWidget):
    """Who is signed in, and Sign out, at the bottom of the sidebar."""

    def __init__(self, who: str | None):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.chip = QLabel(who or "")
        self.chip.setObjectName("UserChip")
        self.chip.setWordWrap(True)
        self.chip.setVisible(bool(who))
        self.signout_btn = QPushButton("Sign out")
        self.signout_btn.setObjectName("SignOut")
        layout.addWidget(self.chip)
        layout.addWidget(self.signout_btn)
        self._who = who

    def set_compact(self, compact: bool) -> None:
        self.chip.setVisible(bool(self._who) and not compact)
        self.signout_btn.setText("⏻" if compact else "Sign out")
        self.signout_btn.setToolTip("Sign out")


class MainWindow(QMainWindow):
    closed = pyqtSignal(bool)           # True when it closed because the user signed out (not to quit the program)

    def __init__(self, db, profile_manager, bus, pipeline=None, audit=None,
                 staging_dir="staging", backup_service=None,
                 cddvd_channel=None, central_state=None, sync_service=None,
                 sync_worker=None, parent=None):
        super().__init__(parent)
        self._signed_out = False
        self.db = db
        self.profile_manager = profile_manager
        self.bus = bus
        self._unknown_label_batches: set[str] = set()
        bus.job_progress.connect(self._on_pipeline_event)
        self.pipeline = pipeline
        self.audit = audit
        self.staging_dir = staging_dir
        user = Session.current_user()

        from version import __version__
        self.setWindowTitle(f"DOK Scanner {__version__} — Batch Document Capture")
        self.setWindowIcon(icon("fa5s.file-import", color=ACCENT))
        fit_to_screen(self, DEFAULT_W, DEFAULT_H, share=0.94)
        self._remember_geometry = False

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        # -- sidebar navigation: brand on top, the user and Sign out below -----------
        self.tabs = SideNav()
        self.tabs.setIconSize(QSize(18, 18))
        brand = BrandBlock()
        self.tabs.set_brand(brand)
        footer = UserBlock(
            None if user is None else
            f"{user.username}  ·  {ROLE_LABELS.get(user.role, user.role)}")
        self.signout_btn = footer.signout_btn
        self.signout_btn.clicked.connect(self.sign_out)
        self.tabs.set_footer(footer)
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
        self.cover_sheets_button = QPushButton("  Print cover sheets")
        self.cover_sheets_button.setIcon(icon("fa5s.barcode"))
        self.cover_sheets_button.setEnabled(
            user is not None and user.can("scan.run"))
        self.cover_sheets_button.clicked.connect(self.open_cover_sheets)
        bar.addWidget(self.cover_sheets_button)
        hint = QLabel("Import files, watch a hot folder, or acquire from "
                      "a TWAIN/WIA scanner.")
        mark(hint, "muted")
        hint.setWordWrap(True)          # a long one-liner made the tab 1138 px wide
        bar.addWidget(hint, stretch=1)
        scan_layout.addLayout(bar)
        self.viewer = ImageViewer()
        scan_layout.addWidget(self.viewer, stretch=1)
        self.tabs.addTab(scan_tab, nav_icon("fa5s.file-import"), "Scan")

        # QC review tab
        self.qc_panel = QCReviewPanel(db, audit, user, pipeline=pipeline)
        self.tabs.addTab(self.qc_panel, nav_icon("fa5s.check-double"),
                         "QC Review")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # Index tab (zones from the first active project, if any)
        zones = []
        ocr_mode = "zone"
        readable, _unreadable = readable_projects(db.list_projects("active"))
        if readable:
            cfg = readable[0][1]
            zones = all_zones(cfg.get("ocr", {}))
            ocr_mode = cfg.get("ocr", {}).get("mode", "zone")
        self.index_editor = IndexEditor(
            db, zones, audit, user, full_page=(ocr_mode == "full_page"),
            zones_for_project=self._zones_for_project,
            ocr_cfg_for_project=self._ocr_cfg_for_project)
        self.index_editor.fields_saved.connect(self._on_fields_saved)
        self.index_editor.page_types_saved.connect(self._on_page_types_saved)
        self.tabs.addTab(self.index_editor, nav_icon("fa5s.tags"), "Index")

        # Search tab: every role may find documents (an operator only those
        # of batches they scanned)
        if user is not None and user.can("document.search"):
            from ui.search_panel import SearchPanel
            self.search_panel = SearchPanel(db, user, audit=audit)
            self.search_panel.edit_requested.connect(self.show_in_index)
            self.tabs.addTab(self.search_panel, nav_icon("fa5s.search"), "Search")

        # Job monitor tab
        self.job_monitor = JobMonitor(bus)
        self.tabs.addTab(self.job_monitor, nav_icon("fa5s.tasks"), "Job Monitor")

        # Reports tab — report.view roles (admin, qc_officer, viewer)
        if user is not None and user.can("report.view"):
            from ui.reports_panel import ReportsPanel
            self.reports_panel = ReportsPanel(db, user)
            self.tabs.addTab(self.reports_panel, nav_icon("fa5s.chart-bar"),
                             "Reports")

        # Workflow designer — project.edit only (HLD §17.5 role gate)
        if user is not None and user.can("project.edit"):
            self.designer = WorkflowDesigner(db, profile_manager, user,
                                            bus=bus)
            self.tabs.addTab(self.designer, nav_icon("fa5s.project-diagram"),
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
            self.tabs.addTab(self.admin_panel, nav_icon("fa5s.user-shield"),
                             "Admin")

        # Custody tab - custody.manage (admin, operator, qc_officer)
        if user is not None and user.can("custody.manage"):
            from ui.custody_panel import CustodyPanel
            self.custody_panel = CustodyPanel(db, audit, user)
            self.tabs.addTab(self.custody_panel, nav_icon("fa5s.boxes"),
                             "Custody")

        # a method, not a lambda: Qt drops a method's connection when the window is destroyed (after Sign out), while
        # a lambda would stay connected and touch the deleted panel at the next finished document
        bus.qc_ready.connect(self._on_qc_ready)
        # Central notices are for every role (the Central tab is admin-only).
        self._central_state = central_state
        self.reenroll_label = QLabel(REENROLL_NOTICE)
        self.reenroll_label.setTextFormat(Qt.TextFormat.PlainText)
        self.reenroll_label.hide()
        self.statusBar().addPermanentWidget(self.reenroll_label)
        if central_state is not None:
            bus.central_synced.connect(self._on_central_synced)
            self._refresh_central_notices()
        self.statusBar().showMessage(
            f"Signed in as {user.username} "
            f"({ROLE_LABELS.get(user.role, user.role)})" if user else
            "Not signed in")

    @pyqtSlot(object)
    def _on_central_synced(self, result) -> None:
        self._refresh_central_notices()

    def _refresh_central_notices(self) -> None:
        self.reenroll_label.setVisible(
            self._central_state is not None
            and self._central_state.settings.auth_failed)

    def _zones_for_project(self, project_id: str):
        """(zones, full_page) of one project's stored config, or None."""
        row = self.db.get_project(project_id)
        cfg = load_config(row) if row is not None else None
        if cfg is None:
            return None
        ocr = cfg.get("ocr", {})
        return all_zones(ocr), ocr.get("mode", "zone") == "full_page"

    def _ocr_cfg_for_project(self, project_id: str):
        row = self.db.get_project(project_id)
        cfg = load_config(row) if row is not None else None
        return None if cfg is None else cfg.get("ocr", {})

    def _active_zones(self) -> list:
        for row in self.db.list_projects("active"):
            cfg = load_config(row)
            if cfg is None:
                continue
            zones = all_zones(cfg.get("ocr", {}))
            if zones:
                return zones
        return []

    def _active_ocr_mode(self) -> str:
        """The first active project's ocr.mode ("zone" or "full_page"),
        mirroring _active_zones()'s "first active project" logic — kept
        as a separate lookup so the two can disagree on which project
        each pulls from without changing _active_zones()'s return type."""
        for row in self.db.list_projects("active"):
            cfg = load_config(row)
            if cfg is None:
                continue
            zones = all_zones(cfg.get("ocr", {}))
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
        elif widget is getattr(self, "search_panel", None):
            self.search_panel.reload_choices()

    def restore_geometry_or_default(self) -> None:
        """Open where the operator left the window (if that is still on a screen),
        otherwise at a size that suits this screen; remember it from now on."""
        from PyQt6.QtCore import QSettings
        self._remember_geometry = True
        saved = QSettings("DOKScanner", "DOKScanner").value("window/geometry")
        if saved is not None and self.restoreGeometry(saved):
            return
        fit_to_screen(self, DEFAULT_W, DEFAULT_H, share=0.94)

    def start_idle_lock(self, app, minutes: float | None = None) -> None:
        """Lock the session after `minutes` without keyboard or mouse use (0 = never)."""
        from ui.idle_lock import IdleGuard, configured_minutes
        self._idle = IdleGuard(configured_minutes() if minutes is None else minutes, self)
        self._idle.idle.connect(self.lock_session)
        self._idle.start(app)

    def lock_session(self) -> None:
        """Cover the window until the signed-in user types their password; a wrong
        password three times, or Sign out, ends the session."""
        from ui.idle_lock import UnlockDialog
        user = Session.current_user()
        if user is None or getattr(self, "_locked", False):
            return
        self._locked = True
        self.db.append_audit(None, user.username, "session_locked", {})
        try:
            dialog = UnlockDialog(self.db, user.username, self)
            ok = dialog.exec() == dialog.DialogCode.Accepted
        finally:
            self._locked = False
        if ok:
            self.db.append_audit(None, user.username, "session_unlocked", {})
            if getattr(self, "_idle", None) is not None:
                self._idle.rearm()
        else:
            self.sign_out()

    def _on_qc_ready(self, _doc_id: str) -> None:
        self.qc_panel.refresh()

    def closeEvent(self, event) -> None:
        if getattr(self, "_remember_geometry", False):
            from PyQt6.QtCore import QSettings
            QSettings("DOKScanner", "DOKScanner").setValue("window/geometry", self.saveGeometry())
        super().closeEvent(event)
        if event.isAccepted():
            self.closed.emit(self._signed_out)

    def sign_out(self) -> None:
        """End the session. The program stays open and shows the sign-in page again (see main.py); closing the
        window any other way quits the program."""
        Session.logout(self.db)
        self._signed_out = True
        self.close()

    def show_in_index(self, document_id: str) -> None:
        """Open a document in the Index tab (from a search result)."""
        self.tabs.setCurrentWidget(self.index_editor)
        if not self.index_editor.select_document(document_id):
            QMessageBox.information(
                self, "Document not listed",
                "That document could not be found in the Index list.")

    def _on_page_types_saved(self, document_id: str) -> None:
        if self.pipeline is not None:
            self.pipeline.reissue_page_types(document_id)

    def _on_fields_saved(self, document_id: str, _fields: dict) -> None:
        """A corrected index reaches the bank too: an already exported
        document gets its index file written and delivered again."""
        if self.pipeline is not None:
            self.pipeline.reissue_index(document_id)

    def _on_pipeline_event(self, event: dict) -> None:
        if event.get("stage") == "custody" and event.get("status") == "unknown_label":
            self.warn_unknown_custody_label(event.get("batch_id", ""),
                                            event.get("page_no"),
                                            event.get("left_out", True))
        elif event.get("stage") == "batch" and event.get("status") == "no_documents":
            self.warn_no_documents(event.get("pages", 0), event.get("reasons") or {})

    _REASON_TEXT = {"custody_label": "custody label sheet",
                    "separator": "barcode separator sheet",
                    "blank": "blank page"}

    def warn_no_documents(self, pages: int, reasons: dict) -> None:
        """A batch whose every page was left out has nothing to review: say so
        and why, instead of leaving the operator to look for documents."""
        counts: dict[str, int] = {}
        for why in reasons.values():
            counts[why] = counts.get(why, 0) + 1
        detail = ", ".join(f"{n} {self._REASON_TEXT.get(why, why)}"
                           for why, n in sorted(counts.items()))
        box = QMessageBox(
            QMessageBox.Icon.Warning, "No documents created",
            f"None of the {pages} scanned page(s) became a document ({detail}). "
            "Nothing was added to QC Review or exported. If these pages were "
            "real documents, check the project's separation settings.",
            parent=self)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setModal(False)
        box.show()
        self._no_documents_box = box

    def warn_unknown_custody_label(self, batch_id: str, page_no,
                                   left_out: bool = True) -> None:
        """One non-blocking warning per batch: a custody label QR that no
        bundle in this database owns was scanned (the page was left out of
        the documents)."""
        if batch_id in self._unknown_label_batches:
            return
        self._unknown_label_batches.add(batch_id)
        box = QMessageBox(
            QMessageBox.Icon.Warning, "Unknown custody label",
            f"Page {page_no} of the batch you are scanning carries a custody "
            "label that this system does not know (another PC's bundle, a "
            "mistyped or reissued sheet). "
            + ("It was left out of the documents" if left_out
               else "The page was kept as a document")
            + " and no custody checkpoint was recorded. Check the bundle in "
            "the Custody tab.", parent=self)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setModal(False)
        box.show()
        self._unknown_label_box = box

    def report_unreadable_projects(self) -> int:
        """At startup: tell the operator now about projects that cannot be
        read, not only when they open the scan wizard. Returns how many."""
        _readable, unreadable = readable_projects(self.db.list_projects("active"))
        if unreadable:
            self._warn_unreadable_projects(unreadable)
        return len(unreadable)

    def open_cover_sheets(self) -> None:
        from ui.cover_sheet_dialog import CoverSheetDialog
        CoverSheetDialog(self).exec()

    def open_scan_wizard(self) -> None:
        readable, unreadable = readable_projects(
            self.db.list_projects("active"))
        projects = [{"project_id": row["project_id"], "name": row["name"],
                     "config": cfg} for row, cfg in readable]
        if unreadable:
            self._warn_unreadable_projects(unreadable)
        if not projects and unreadable:
            return
        wizard = ScanWizard(projects, self.staging_dir, self)
        wizard.batch_ready.connect(self.start_batch)
        wizard.exec()

    def _warn_unreadable_projects(self, names: list[str]) -> None:
        """ONE warning naming the projects that could not be read (names
        only, never config content), as plain text."""
        shown = ", ".join(names[:_MAX_REFUSED_LINES])
        if len(names) > _MAX_REFUSED_LINES:
            shown += f" and {len(names) - _MAX_REFUSED_LINES} more"
        box = QMessageBox(
            QMessageBox.Icon.Warning, "Projects not listed",
            f"{len(names)} project(s) are unreadable and are not listed: "
            f"{shown}. Open the Workflow Designer to repair them.", parent=self)
        box.setTextFormat(Qt.TextFormat.PlainText)
        repair_btn = None
        if self._can_repair_projects():
            repair_btn = box.addButton("Repair…", QMessageBox.ButtonRole.ActionRole)
        box.exec()
        if repair_btn is not None and box.clickedButton() is repair_btn:
            self.open_project_repair()

    def _can_repair_projects(self) -> bool:
        user = Session.current_user()
        return user is not None and user.can("project.edit")             and self.profile_manager is not None

    def open_project_repair(self) -> None:
        from ui.project_repair_dialog import ProjectRepairDialog
        dialog = ProjectRepairDialog(self.db, self.profile_manager, self.audit,
                                     Session.current_user(), self)
        dialog.exec()

    def report_refused_batches(self, refused: list) -> None:
        """Tell the operator which interrupted batches were not resumed at
        startup (Pipeline.refused_batches: (batch_id, message) pairs, both
        value-free). Shows nothing for an empty list."""
        if not refused:
            return
        lines = [f"{batch_id}: {message}"
                 for batch_id, message in refused[:_MAX_REFUSED_LINES]]
        if len(refused) > _MAX_REFUSED_LINES:
            lines.append(f"and {len(refused) - _MAX_REFUSED_LINES} more")
        QMessageBox.warning(
            self, "Batches not recovered",
            "\n".join(lines) + "\n\nThese batches were interrupted and "
            "their project is not available, so they were not resumed. Fix "
            "the project and rescan.")

    def start_batch(self, batch_id: str, jobs: list) -> None:
        user = Session.current_user()
        if self.pipeline is not None:
            try:
                self.pipeline.submit_batch(
                    batch_id, jobs,
                    operator=user.username if user else "unknown")
            except (ProjectUnavailable, ValueError) as e:
                # str(e) only: the chained cause can contain config values
                log.warning("batch %s refused: %s", batch_id, e)
                if isinstance(e, ProjectUnavailable):
                    QMessageBox.critical(
                        self, "Project not available",
                        f"This batch was not started: {e}\n\nNothing was "
                        "recorded. Check the project in the Workflow "
                        "Designer, then scan again.")
                else:
                    QMessageBox.critical(
                        self, "Batch not started",
                        f"This batch could not be started: {e}\n\nNothing "
                        "was recorded.")
                return
        if self.audit:
            self.audit.log("scan", user.username if user else "unknown",
                           batch_id=batch_id, pages=len(jobs))
        self.viewer.load_pages([j.image_path for j in jobs])
