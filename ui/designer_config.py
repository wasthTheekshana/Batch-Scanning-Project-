"""Moving a project's settings into the form and back (and the QC presets).

A mixin of ui.workflow_designer.WorkflowDesigner: it relies on the widgets that class builds.
"""
from __future__ import annotations

import json

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QListWidgetItem

from ui.designer_common import QC_PRESETS


class ConfigMixin:
    def _apply_qc_preset(self, text: str) -> None:
        """Fill all five QC threshold fields at once from a named
        strictness preset. "Custom" has no preset dict, so it's a no-op -
        it only ever appears when the current values don't match any
        preset (see _bind_config), never something to "apply"."""
        preset = QC_PRESETS.get(text)
        if preset is None:
            return
        self.pass_score.setValue(preset["pass_score"])
        self.blur_warn.setValue(preset["blur_warn_threshold"])
        self.blur_fail.setValue(preset["blur_fail_threshold"])
        self.dpi_warn.setValue(preset["dpi_warn"])
        self.dpi_fail.setValue(preset["dpi_fail"])

    def _bind_config(self) -> None:
        cfg = self.config
        self.version_label.setText(
            f"Editing v{cfg['version']} · status {cfg['status']} · "
            f"archived versions: "
            f"{self.pm.list_versions(cfg['project_id']) or 'none'}")
        for key, cb in self.feature_checks.items():
            cb.setChecked(bool(cfg["features"].get(key)))
        for key, cb in self.strategy_checks.items():
            cb.setChecked(key in cfg["separation"]["strategy"])
        self.fixed_pages.setValue(cfg["separation"].get("fixed_pages") or 0)
        self.barcode_role.setCurrentIndex(max(0, self.barcode_role.findData(
            cfg["separation"].get("barcode_role", "first_page"))))
        self.barcode_pattern.setText(cfg["separation"].get("barcode_pattern", ""))
        self.drop_blanks.setChecked(bool(cfg["separation"].get("drop_blank_pages")))
        qc = cfg["qc"]
        self.qc_enabled.setChecked(qc["enabled"])
        for stage, cb in self.qc_stage_checks.items():
            cb.setChecked(stage in qc["stages"])
        self.pass_score.setValue(qc.get("pass_score", 80))
        self.blur_warn.setValue(int(qc.get("blur_warn_threshold", 80)))
        self.blur_fail.setValue(int(qc.get("blur_fail_threshold", 30)))
        self.dpi_warn.setValue(int(qc.get("dpi_warn", 200)))
        self.dpi_fail.setValue(int(qc.get("dpi_fail", 150)))
        current_thresholds = {
            "pass_score": self.pass_score.value(),
            "blur_warn_threshold": self.blur_warn.value(),
            "blur_fail_threshold": self.blur_fail.value(),
            "dpi_warn": self.dpi_warn.value(), "dpi_fail": self.dpi_fail.value()}
        matched_preset = next(
            (name for name, preset in QC_PRESETS.items()
             if preset == current_thresholds), "Custom")
        self.qc_preset.blockSignals(True)
        self.qc_preset.setCurrentText(matched_preset)
        self.qc_preset.blockSignals(False)
        self.dpi.setValue(cfg["scanner"]["dpi"])
        hot = cfg["scanner"].get("hot_folder") or {}
        self.hot_enabled.setChecked(bool(hot.get("enabled")))
        self.hot_path.setText(str(hot.get("path") or ""))
        self.hot_path.setEnabled(self.can_edit and self.hot_enabled.isChecked())
        self.color.setCurrentText(cfg["scanner"]["color"])
        fmt = cfg["export"]["format"]
        self.export_format.setCurrentText("searchable_pdf" if fmt == "pdf_a" else fmt)
        self.filename_template.setText(cfg["export"]["filename_template"])
        self.output_path.setText(cfg["export"]["output_path"])
        self.delivery_path.setText(
            cfg["delivery"]["local_folder"].get("path", ""))
        index = cfg["export"].get("index") or {}
        self.index_enabled.setChecked(bool(index.get("enabled")))
        self.index_format.setCurrentIndex(max(
            0, self.index_format.findData(index.get("format", "csv"))))
        self.index_running.setChecked(bool(index.get("running_csv")))
        self.ocr_mode.blockSignals(True)
        self.ocr_mode.setCurrentText(
            "Full-page (label-anchored regex)"
            if cfg.get("ocr", {}).get("mode") == "full_page"
            else "Zone-based (draw regions)")
        self.ocr_mode.blockSignals(False)
        self.zone_list.clear()
        for zone in cfg.get("ocr", {}).get("zones", []):
            self._append_zone(zone)
        self.type_list.clear()
        for doc_type in cfg.get("ocr", {}).get("document_types", []):
            self._append_type(doc_type)
        sftp = cfg["delivery"].get("sftp", {})
        # after the feature toggles above (their signals also set these)
        self.sftp_enabled.setChecked(bool(sftp.get("enabled")))
        self.cd_enabled.setChecked(
            bool(cfg["delivery"].get("cd_dvd", {}).get("enabled")))
        self.sftp_host.setText(sftp.get("host", ""))
        self.sftp_port.setValue(int(sftp.get("port", 22)))
        self.sftp_user.setText(sftp.get("username", ""))
        self.sftp_password.setText(sftp.get("password", ""))
        self.sftp_key_file.setText(sftp.get("key_file", ""))
        self.sftp_remote_path.setText(sftp.get("remote_path", ""))
        self._refresh_rules()

    def _refresh_rules(self) -> None:
        self.rule_list.clear()
        for rule in self.config.get("rules", {}).get("rules", []):
            item = QListWidgetItem(f"{rule['id']} — {rule['name']}")
            item.setData(Qt.ItemDataRole.UserRole, rule)
            self.rule_list.addItem(item)

    def collect_config(self) -> dict:
        """Read every widget back into a config dict (pure; used by save
        and by tests)."""
        cfg = json.loads(json.dumps(self.config))  # deep copy
        cfg["features"].update({k: cb.isChecked()
                                for k, cb in self.feature_checks.items()})
        cfg["separation"]["strategy"] = [
            k for k, cb in self.strategy_checks.items() if cb.isChecked()]
        cfg["separation"]["fixed_pages"] = self.fixed_pages.value() or None
        cfg["separation"]["barcode_role"] = self.barcode_role.currentData()
        if self.drop_blanks.isChecked():
            cfg["separation"]["drop_blank_pages"] = True
        else:
            cfg["separation"].pop("drop_blank_pages", None)
        pattern = self.barcode_pattern.text().strip()
        if pattern:
            cfg["separation"]["barcode_pattern"] = pattern
        else:
            cfg["separation"].pop("barcode_pattern", None)
        cfg["qc"]["enabled"] = self.qc_enabled.isChecked()
        cfg["qc"]["stages"] = [s for s, cb in self.qc_stage_checks.items()
                               if cb.isChecked()]
        cfg["qc"]["pass_score"] = self.pass_score.value()
        cfg["qc"]["blur_warn_threshold"] = self.blur_warn.value()
        cfg["qc"]["blur_fail_threshold"] = self.blur_fail.value()
        cfg["qc"]["dpi_warn"] = self.dpi_warn.value()
        cfg["qc"]["dpi_fail"] = self.dpi_fail.value()
        cfg["scanner"]["dpi"] = self.dpi.value()
        if self.hot_enabled.isChecked() or self.hot_path.text().strip():
            cfg["scanner"]["hot_folder"] = {
                "enabled": self.hot_enabled.isChecked(),
                "path": self.hot_path.text().strip()}
        else:
            cfg["scanner"].pop("hot_folder", None)
        cfg["scanner"]["color"] = self.color.currentText()
        cfg["export"]["format"] = self.export_format.currentText()
        cfg["export"]["filename_template"] = self.filename_template.text()
        cfg["export"]["output_path"] = self.output_path.text()
        if self.index_enabled.isChecked():
            cfg["export"]["index"] = {
                "enabled": True, "format": self.index_format.currentData(),
                "running_csv": self.index_running.isChecked()}
        else:
            cfg["export"].pop("index", None)
        cfg["delivery"]["local_folder"]["path"] = self.delivery_path.text()
        # delivery on/off comes from the Enabled checkboxes (local to this
        # PC); the feature toggles only pre-set them in the form
        cfg["delivery"]["sftp"]["enabled"] = self.sftp_enabled.isChecked()
        cfg["delivery"]["cd_dvd"]["enabled"] = self.cd_enabled.isChecked()
        cfg["delivery"]["sftp"].update({
            "host": self.sftp_host.text().strip(),
            "port": self.sftp_port.value(),
            "username": self.sftp_user.text().strip(),
            "password": self.sftp_password.text(),
            "key_file": self.sftp_key_file.text().strip(),
            "remote_path": self.sftp_remote_path.text().strip(),
        })
        cfg["ocr"]["mode"] = "full_page" if self._is_full_page_mode() else "zone"
        cfg["ocr"]["zones"] = [
            self.zone_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.zone_list.count())]
        types = [self.type_list.item(i).data(Qt.ItemDataRole.UserRole)
                 for i in range(self.type_list.count())]
        if types:
            cfg["ocr"]["document_types"] = types
        else:
            cfg["ocr"].pop("document_types", None)
        cfg["rules"]["rules"] = [
            self.rule_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.rule_list.count())]
        return cfg

    def _index_toggled(self, on: bool) -> None:
        """The format and running-file choices only matter when an index
        file is written."""
        self.index_format.setEnabled(on and self.can_edit)
        self.index_running.setEnabled(on and self.can_edit)
