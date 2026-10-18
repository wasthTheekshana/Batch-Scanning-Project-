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

from datetime import datetime, timezone

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot
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


def age_text(stamp: str | None, now: datetime | None = None) -> str:
    """"5 minutes", "3 hours" ... how long ago an ISO time was."""
    try:
        then = datetime.fromisoformat(stamp or "")
    except ValueError:
        return "an unknown time"
    seconds = max(0, int(((now or datetime.now(timezone.utc)) - then).total_seconds()))
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            n = seconds // size
            return f"{n} {unit}{'s' if n != 1 else ''}"
    return "under a minute"


def documents_text(stats: dict, now: datetime | None = None) -> str:
    """What the document reporting is doing, in a sentence or two (counts and times only)."""
    lines = []
    if stats["waiting"]:
        lines.append(f"{stats['waiting']} document(s) waiting to be reported to central "
                     f"(the oldest for {age_text(stats['oldest'], now)}). Scanning is not affected.")
        if stats["last_error"]:
            lines.append(f"Last problem: {_safe(stats['last_error'], 120)}. It will try again by itself.")
    elif not stats["failed"]:
        lines.append("All documents have been reported to central."
                     if stats["last_sent_at"] else "No documents have been reported to central yet.")
    if stats["failed"]:
        lines.append(f"{stats['failed']} document(s) were refused by central (for example, a project that is not "
                     "assigned to this PC). Use Retry refused once that is fixed.")
    if stats["last_sent_at"] and stats["waiting"]:
        lines.append(f"Last report {age_text(stats['last_sent_at'], now)} ago.")
    return "\n".join(lines)


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

        self.docs_label = QLabel()
        self.docs_label.setTextFormat(Qt.TextFormat.PlainText)
        self.docs_label.setWordWrap(True)
        layout.addWidget(self.docs_label)
        docs_row = QHBoxLayout()
        self.send_old_btn = QPushButton("Send existing documents")
        self.send_old_btn.setToolTip("Report the documents already on this PC (the index fields and PDF "
                                     "details, never the PDF itself).")
        self.send_old_btn.clicked.connect(self.send_existing)
        self.retry_btn = QPushButton("Retry refused")
        self.retry_btn.clicked.connect(self.retry_refused)
        docs_row.addWidget(self.send_old_btn)
        docs_row.addWidget(self.retry_btn)
        docs_row.addStretch(1)
        layout.addLayout(docs_row)
        self._docs_timer = QTimer(self)
        self._docs_timer.timeout.connect(self.refresh_documents)
        self._docs_timer.start(15000)

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
        self.refresh_documents()

    def refresh_documents(self) -> None:
        stats = self.db.outbox_stats() if self.db is not None and hasattr(self.db, "outbox_stats") else None
        registered = self.state.is_registered()
        shown = registered and stats is not None
        self.docs_label.setVisible(shown)
        self.send_old_btn.setVisible(shown)
        self.retry_btn.setVisible(shown and bool(stats and stats["failed"]))
        if shown and stats is not None:
            self.docs_label.setText(documents_text(stats))
            self.send_old_btn.setEnabled(self._can)
            self.retry_btn.setEnabled(self._can)

    def send_existing(self) -> None:
        if not self._can or self.db is None:
            return
        answer = QMessageBox.question(
            self, "Send existing documents",
            "Report every document already on this PC to central? Only the index fields and the PDF details "
            "are sent, never the PDF. Documents scanned from now on are reported by themselves.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.db.outbox_enqueue_existing()
        self._wake_documents()
        self.refresh_documents()

    def retry_refused(self) -> None:
        if self._can and self.db is not None:
            self.db.outbox_retry_failed()
            self._wake_documents()
            self.refresh_documents()

    def _wake_documents(self) -> None:
        sender = getattr(self.service, "document_worker", None)
        if sender is not None:
            sender.push_now()

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
