"""Choosing a project, loading it into the form, and the centrally managed state.

A mixin of ui.workflow_designer.WorkflowDesigner: it relies on the widgets that class builds.
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import pyqtSlot
from PyQt6.QtWidgets import QGroupBox, QPushButton

from projects.profile_manager import ProjectConfigError

from ui.designer_common import SAVE_LABEL, SAVE_LOCAL_LABEL

log = logging.getLogger("ui.workflow_designer")      # the same logger as before the split


class SelectionMixin:
    def _fill_combo(self) -> None:
        """Rebuild the combo items from the database without emitting
        signals (the caller decides whether the form is rebound)."""
        rows = self.db.list_projects()          # may raise: read before blocking
        self.project_combo.blockSignals(True)
        try:
            self.project_combo.clear()
            for row in rows:
                self.project_combo.addItem(
                    f"{row['name']} (v{row['version']})", row["project_id"])
        finally:
            self.project_combo.blockSignals(False)

    def refresh_projects(self) -> None:
        self._fill_combo()
        if self.project_combo.count():
            self.project_combo.setCurrentIndex(0)
            self.load_selected()

    @pyqtSlot(object)
    def on_central_synced(self, result) -> None:
        """A sync adopted or unassigned projects: refresh ONLY the project
        list and the managed state. The form is never rebound, so edits the
        operator has not saved yet survive."""
        try:
            self._apply_central_sync(result)
        except Exception as exc:        # a Qt slot must never raise
            log.warning("designer refresh after central sync failed: %s",
                        type(exc).__name__)

    def _apply_central_sync(self, result) -> None:
        applied, unassigned = list(result.applied), list(result.unassigned)
        if not applied and not unassigned:
            return
        current = self.project_combo.currentData()
        self._fill_combo()
        if not self.project_combo.count():
            return
        idx = self.project_combo.findData(current) if current else -1
        if idx < 0:
            # nothing was selected before (or it vanished): nothing to lose
            self.project_combo.setCurrentIndex(0)
            self.load_selected()
            return
        self.project_combo.blockSignals(True)
        try:
            self.project_combo.setCurrentIndex(idx)
        finally:
            self.project_combo.blockSignals(False)
        if current in applied:
            self.central_notice.show()
        if current in applied or current in unassigned:
            self._update_managed_state()

    def load_selected(self) -> None:
        self.central_notice.hide()
        project_id = self.project_combo.currentData()
        if not project_id:
            return
        try:
            config = self.pm.load(project_id)
        except (ProjectConfigError, ValueError, OSError) as exc:
            # never show or log the error text: it can quote the file
            log.warning("project %s could not be loaded in the designer (%s)",
                        project_id, type(exc).__name__)
            self._show_unreadable()
            return
        self.unreadable_banner.hide()
        self.form_body.setEnabled(True)
        for b in self._action_buttons():
            b.setEnabled(self.can_edit)
        self.config = config
        self._bind_config()
        self._update_managed_state()

    def _action_buttons(self) -> tuple[QPushButton, ...]:
        return (self.new_from_template_btn, self.clone_btn, self.rollback_btn,
                self.save_btn, self.save_template_btn)

    def _clear_form(self) -> None:
        """Blank the free-text fields and lists so the previously shown
        project's values (paths, SFTP login...) are not left under the
        banner."""
        for edit in (self.filename_template, self.output_path,
                     self.delivery_path, self.sftp_host, self.sftp_user,
                     self.sftp_password, self.sftp_key_file,
                     self.sftp_remote_path):
            edit.clear()
        self.zone_list.clear()
        self.type_list.clear()
        self.rule_list.clear()

    def _show_unreadable(self) -> None:
        """The selected project's file cannot be read: nothing to edit, so
        the form and the project actions stay off until it is repaired."""
        self.config = None
        self._clear_form()
        self.version_label.setText("")
        self.managed_banner.hide()
        self.unreadable_banner.show()
        self.form_body.setEnabled(False)
        for b in self._action_buttons():
            b.setEnabled(False)
        self.new_from_template_btn.setEnabled(self.can_edit)  # needs no project

    def _is_managed(self) -> bool:
        """Evaluated on demand (never cached): central may assign or
        unassign the shown project at any time."""
        if not self.config:
            return False
        rec = self.db.get_central_project(self.config["project_id"])
        return rec is not None and bool(rec["managed"])

    def _update_managed_state(self) -> None:
        """A managed project: only the PC-local sections (scanner, export,
        delivery = 'Scanner & export' and 'SFTP delivery') stay editable.
        Everything else belongs to central and is shown read-only."""
        managed = self._is_managed()
        self.managed_banner.setVisible(managed)
        self.save_btn.setText(SAVE_LOCAL_LABEL if managed else SAVE_LABEL)
        for box in self._central_boxes():
            box.setEnabled(not managed)

    def _central_boxes(self) -> tuple[QGroupBox, ...]:
        return (self.feature_box, self.sep_box, self.qc_box, self.zone_box,
                self.type_box, self.rule_box)
