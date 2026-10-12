"""Admin > Central: enrol this PC with the central service and show sync
status (spec: sync client). Admin-only (user.manage).

Registration makes a network call, so it runs on a short-lived daemon thread
and its outcome comes back to the UI thread through a signal. Text coming
from the server (project names, error reasons) is shown as plain text and
capped. The API key is never read, shown or copied by this tab.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QMessageBox, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from core.central_client import (CentralRegistrationError, CentralUnavailable)
from core.central_sync import SyncResult
from ui.theme import mark

log = logging.getLogger(__name__)

MAX_TEXT = 200       # cap for any single piece of server-supplied text
MAX_ERRORS_SHOWN = 5
APPLIED_TEXT = ("Updated project settings were received; "
                "they apply to the next scan batch.")


def _safe(value, limit: int = MAX_TEXT) -> str:
    """Untrusted text for display: printable characters only, bounded."""
    text = "".join(c if c.isprintable() else " " for c in str(value)[:limit * 2])
    return text if len(text) <= limit else text[:limit] + "…"


class CentralTab(QWidget):
    # None on success, otherwise the exception raised by service.register
    registration_finished = pyqtSignal(object)

    def __init__(self, state, service, worker, current_user, bus=None,
                 parent=None):
        super().__init__(parent)
        self.state, self.service, self.worker = state, service, worker
        self.db = getattr(service, "db", None)
        self._last_result: SyncResult | None = None
        self._registering = False
        can = current_user is not None and current_user.can("user.manage")
        self._can = can

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.url_edit = QLineEdit(state.settings.server_url)
        self.url_edit.setPlaceholderText("https://central.example.com")
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("one-time enrollment code")
        form.addRow("Central server", self.url_edit)
        form.addRow("Enrollment code", self.code_edit)
        layout.addLayout(form)

        row = QHBoxLayout()
        self.register_btn = QPushButton("Register this PC")
        mark(self.register_btn, "primary")
        self.register_btn.clicked.connect(self.register)
        self.sync_btn = QPushButton("Sync now")
        self.sync_btn.clicked.connect(self.sync_now)
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(self.disconnect)
        for b in (self.register_btn, self.sync_btn, self.disconnect_btn):
            row.addWidget(b)
        layout.addLayout(row)

        self.status_label = QLabel()
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.projects_table = QTableWidget(0, 3)
        self.projects_table.setHorizontalHeaderLabels(
            ["Project", "Central version", "Managed"])
        header = self.projects_table.horizontalHeader()
        header.setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.projects_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.projects_table, stretch=1)

        self.registration_finished.connect(self._on_registration_finished)
        if bus is not None:
            bus.central_synced.connect(self._on_synced)
        self.refresh_status()

    # -- status ---------------------------------------------------------------
    def status_text(self, result: SyncResult | None = None) -> str:
        s = self.state.settings
        lines = ["Registering…"] if self._registering else []
        if not self.state.is_registered():
            lines.append("This PC is not registered with a central server.")
            return "\n".join(lines)
        lines.append(f"Registered as PC {_safe(s.pc_id)} at "
                     f"{_safe(s.server_url)}.")
        if s.auth_failed:
            lines.append("Central rejected this PC's key. Ask an administrator "
                         "for a new enrollment code and register again.")
        elif s.last_sync_at:
            lines.append(f"Last sync {_safe(s.last_sync_at)}: "
                         f"{_safe(s.last_sync_status)}.")
        if result is not None:
            if result.status == "unavailable":
                lines.append(f"Central is unavailable ({_safe(result.message)});"
                             " using the last known configuration.")
            if result.errors:
                shown = list(result.errors.items())[:MAX_ERRORS_SHOWN]
                detail = "; ".join(f"{_safe(k, 60)}: {_safe(v)}"
                                   for k, v in shown)
                extra = len(result.errors) - len(shown)
                if extra > 0:
                    detail += f"; and {extra} more"
                lines.append(f"{len(result.errors)} project(s) were skipped: "
                             + detail)
        if result is not None and result.applied:
            lines.append(APPLIED_TEXT)
        return "\n".join(lines)

    def refresh_status(self, result: SyncResult | None = None) -> None:
        if result is not None:
            self._last_result = result
        registered = self.state.is_registered()
        idle = self._can and not self._registering
        self.status_label.setText(self.status_text(self._last_result))
        self.register_btn.setEnabled(idle)
        self.sync_btn.setEnabled(idle and registered
                                 and not self.state.settings.auth_failed)
        self.disconnect_btn.setEnabled(idle and registered)
        self._fill_projects()

    def _fill_projects(self) -> None:
        rows = []
        if self.db is not None:
            for rec in self.db.list_central_projects():
                p = self.db.get_project(rec["project_id"])
                rows.append((_safe(p["name"] if p else rec["project_id"]),
                             f"v{rec['central_version']}",
                             "yes" if rec["managed"] else "no (unassigned)"))
        self.projects_table.setRowCount(len(rows))
        for r, vals in enumerate(rows):
            for c, v in enumerate(vals):
                self.projects_table.setItem(r, c, QTableWidgetItem(v))

    @pyqtSlot(object)
    def _on_synced(self, result) -> None:
        self.refresh_status(result)

    # -- actions ------------------------------------------------------------------
    def _show_warning(self, text: str) -> None:
        """Plain text always: a message that looks like HTML (server text,
        exception text) must not be rendered as rich text."""
        box = QMessageBox(QMessageBox.Icon.Warning, "Central", text,
                          QMessageBox.StandardButton.Ok, self)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.exec()

    def register(self) -> None:
        if not self._can or self._registering:
            return
        url, code = self.url_edit.text().strip(), self.code_edit.text().strip()
        if not url or not code:
            self._show_warning("Enter the server address and the "
                               "enrollment code.")
            return
        self._registering = True
        self.refresh_status()
        self._run_in_background(lambda: self._register_worker(url, code))

    def _run_in_background(self, fn: Callable[[], None]) -> None:
        """Seam: tests replace this to run (or hold) the work deterministically."""
        threading.Thread(target=fn, name="central-register",
                         daemon=True).start()

    def _register_worker(self, url: str, code: str) -> None:
        """Runs off the UI thread: only touches the service, then signals."""
        outcome: Exception | None = None
        try:
            self.service.register(url, code)
        except Exception as e:  # delivered to the UI thread, never lost
            outcome = e
        try:
            self.registration_finished.emit(outcome)
        except RuntimeError:        # the tab was destroyed while we were away
            pass

    @pyqtSlot(object)
    def _on_registration_finished(self, error) -> None:
        self._registering = False
        if error is None:
            self.code_edit.clear()
            if self.worker is not None:
                self.worker.sync_now()
            self.refresh_status()
            return
        self.refresh_status()
        if isinstance(error, (ValueError, CentralRegistrationError)):
            msg = _safe(error)
        elif isinstance(error, CentralUnavailable):
            msg = ("Could not reach the central server. Check the address "
                   "and the network connection, then try again.")
        else:
            # type name only: the exception text could contain the code
            log.warning("central registration failed: %s", type(error).__name__)
            msg = "Registration failed unexpectedly. See the log for details."
        self._show_warning(msg)

    def sync_now(self) -> None:
        if self._can and self.worker is not None:
            self.worker.sync_now()

    def disconnect(self) -> None:
        if not self._can:
            return
        answer = QMessageBox.question(
            self, "Disconnect",
            "Disconnect this PC from central? Existing projects stay on this "
            "PC but will no longer be updated.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.service.disconnect()
        self._last_result = None
        self.refresh_status()
