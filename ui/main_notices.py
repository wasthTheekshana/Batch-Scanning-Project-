"""Telling the operator things: central notices, left-out pages, unreadable projects, batches not resumed.

A mixin of ui.main_window.MainWindow: it relies on the window's `db`, `profile_manager`, `audit` and notice label.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtWidgets import QMessageBox

from projects.project_store import readable_projects
from security.rbac import Session

_MAX_REFUSED_LINES = 10

REENROLL_NOTICE = "Central: new enrollment code needed"


class NoticesMixin:
    _REASON_TEXT = {"custody_label": "custody label sheet",
                    "separator": "barcode separator sheet",
                    "blank": "blank page"}

    @pyqtSlot(object)
    def _on_central_synced(self, result) -> None:
        self._refresh_central_notices()

    def _refresh_central_notices(self) -> None:
        self.reenroll_label.setVisible(
            self._central_state is not None
            and self._central_state.settings.auth_failed)

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
        return user is not None and user.can("project.edit") and self.profile_manager is not None

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
