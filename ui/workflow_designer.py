"""Workflow designer (HLD §16, Phase 2 §18 weeks 5-6).

Edits the same project JSON the engine runs — the rule builder emits
the exact Phase 1 DSL, validated by RuleEngine before save, so there is
complete backwards compatibility (HLD §8). Saving goes through
ProfileManager.save which versions + archives automatically.
"""
from __future__ import annotations

import json
import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QFormLayout, QGroupBox, QHBoxLayout,
                             QInputDialog, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QMessageBox, QPushButton,
                             QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from core.config_overlay import LOCAL_ONLY_KEYS
from core.profile_manager import ProjectConfigError
from separation.rule_engine import RuleEngine, RuleSetError

log = logging.getLogger(__name__)

WRITE_FAILED_TEXT = ("The project file could not be written. Close other "
                     "programs using it and try again.")
QC_STAGES = ["punch_hole", "rotation", "margin", "color", "advanced"]
STRATEGIES = ["barcode", "blank_page", "zone", "fixed_pages"]
OPERATORS = ["eq", "neq", "contains", "not_contains", "is_empty",
             "is_not_empty", "gt", "lt", "gte", "lte", "matches_regex"]
ACTIONS = ["set_folder", "set_filename", "set_validation_status",
           "add_flag", "set_metadata_field", "trigger_delivery",
           "skip_stage"]
MANAGED_BANNER = ("Managed centrally: only scanner / export / delivery "
                  "can be changed on this PC")
EXPORT_FORMATS = ["searchable_pdf", "image_pdf", "pdf_a", "tiff"]

# QC strictness presets (Enhancement Plan §4.3) - one-click threshold sets
# for document sensitivity (e.g. legal/financial vs routine correspondence).
# "Standard" matches DEFAULT_PROJECT's own qc defaults exactly.
QC_PRESETS = {
    "Strict": {"pass_score": 90, "blur_warn_threshold": 100,
              "blur_fail_threshold": 50, "dpi_warn": 250, "dpi_fail": 200},
    "Standard": {"pass_score": 80, "blur_warn_threshold": 80,
                "blur_fail_threshold": 30, "dpi_warn": 200, "dpi_fail": 150},
    "Lenient": {"pass_score": 65, "blur_warn_threshold": 60,
               "blur_fail_threshold": 15, "dpi_warn": 150, "dpi_fail": 100},
}


class RuleDialog(QDialog):
    """Build one rule: N condition checks joined by AND/OR + N actions."""

    def __init__(self, rule: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Rule editor")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.name = QLineEdit(rule["name"] if rule else "")
        form.addRow("Rule name", self.name)
        self.join = QComboBox()
        self.join.addItems(["AND", "OR"])
        form.addRow("Combine checks with", self.join)
        layout.addLayout(form)

        layout.addWidget(QLabel("Conditions (field · operator · value):"))
        self.checks = QListWidget()
        layout.addWidget(self.checks)
        check_btns = QHBoxLayout()
        add_check = QPushButton("Add condition…")
        del_check = QPushButton("Remove selected")
        add_check.clicked.connect(self.add_check)
        del_check.clicked.connect(
            lambda: self.checks.takeItem(self.checks.currentRow()))
        check_btns.addWidget(add_check)
        check_btns.addWidget(del_check)
        layout.addLayout(check_btns)

        layout.addWidget(QLabel("Actions (type · value):"))
        self.actions = QListWidget()
        layout.addWidget(self.actions)
        action_btns = QHBoxLayout()
        add_action = QPushButton("Add action…")
        del_action = QPushButton("Remove selected")
        add_action.clicked.connect(self.add_action)
        del_action.clicked.connect(
            lambda: self.actions.takeItem(self.actions.currentRow()))
        action_btns.addWidget(add_action)
        action_btns.addWidget(del_action)
        layout.addLayout(action_btns)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if rule:
            cond = rule["condition"]
            checks = cond["checks"] if "operator" in cond else [cond]
            if "operator" in cond and cond["operator"] in ("AND", "OR"):
                self.join.setCurrentText(cond["operator"])
            for c in checks:
                self._append_check(c)
            for a in rule["actions"]:
                self._append_action(a)

    # -- check/action rows ------------------------------------------------------
    def _append_check(self, check: dict) -> None:
        item = QListWidgetItem(
            f"{check['field']}  {check['op']}  {check.get('value', '')}")
        item.setData(Qt.ItemDataRole.UserRole, check)
        self.checks.addItem(item)

    def _append_action(self, action: dict) -> None:
        item = QListWidgetItem(
            f"{action['type']}  →  {json.dumps(action.get('value'))}")
        item.setData(Qt.ItemDataRole.UserRole, action)
        self.actions.addItem(item)

    def add_check(self) -> None:
        field, ok = QInputDialog.getText(
            self, "Condition", "Field path (e.g. extracted_fields.invoice_no):")
        if not ok or not field.strip():
            return
        op, ok = QInputDialog.getItem(self, "Condition", "Operator:",
                                      OPERATORS, 0, False)
        if not ok:
            return
        check = {"field": field.strip(), "op": op}
        if op not in ("is_empty", "is_not_empty"):
            value, ok = QInputDialog.getText(self, "Condition", "Value:")
            if not ok:
                return
            check["value"] = value
        self._append_check(check)

    def add_action(self) -> None:
        action_type, ok = QInputDialog.getItem(self, "Action", "Type:",
                                               ACTIONS, 0, False)
        if not ok:
            return
        if action_type == "set_metadata_field":
            field, ok = QInputDialog.getText(self, "Action", "Field name:")
            if not ok:
                return
            value, ok = QInputDialog.getText(self, "Action", "Field value:")
            if not ok:
                return
            self._append_action({"type": action_type,
                                 "value": {"field": field, "value": value}})
        else:
            value, ok = QInputDialog.getText(self, "Action", "Value:")
            if not ok:
                return
            self._append_action({"type": action_type, "value": value})

    def build_rule(self, rule_id: str) -> dict:
        checks = [self.checks.item(i).data(Qt.ItemDataRole.UserRole)
                  for i in range(self.checks.count())]
        actions = [self.actions.item(i).data(Qt.ItemDataRole.UserRole)
                   for i in range(self.actions.count())]
        condition = checks[0] if len(checks) == 1 else {
            "operator": self.join.currentText(), "checks": checks}
        return {"id": rule_id, "name": self.name.text().strip() or rule_id,
                "condition": condition, "actions": actions}


class WorkflowDesigner(QWidget):
    def __init__(self, db, profile_manager, current_user, parent=None):
        super().__init__(parent)
        self.db = db
        self.pm = profile_manager
        self.user = current_user
        self.config: dict | None = None
        self.can_edit = current_user is not None and \
            current_user.can("project.edit")

        outer = QVBoxLayout(self)

        top = QHBoxLayout()
        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self.load_selected)
        top.addWidget(QLabel("Project:"))
        top.addWidget(self.project_combo, stretch=1)
        self.clone_btn = QPushButton("Clone…")
        self.clone_btn.clicked.connect(self.clone_project)
        self.new_from_template_btn = QPushButton("New from template…")
        self.new_from_template_btn.clicked.connect(self.new_from_template)
        self.rollback_btn = QPushButton("Version history…")
        self.rollback_btn.clicked.connect(self.rollback_dialog)
        self.save_btn = QPushButton("Save (new version)")
        self.save_btn.clicked.connect(self.save)
        self.save_template_btn = QPushButton("Save as template…")
        self.save_template_btn.clicked.connect(self.save_as_template)
        from ui.theme import mark
        mark(self.save_btn, "primary")
        for b in (self.new_from_template_btn, self.clone_btn,
                  self.rollback_btn, self.save_btn, self.save_template_btn):
            b.setEnabled(self.can_edit)
            top.addWidget(b)
        outer.addLayout(top)

        self.version_label = QLabel("")
        outer.addWidget(self.version_label)
        self.managed_banner = QLabel(MANAGED_BANNER)
        self.managed_banner.setWordWrap(True)
        self.managed_banner.setTextFormat(Qt.TextFormat.PlainText)
        self.managed_banner.hide()
        outer.addWidget(self.managed_banner)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        scroll.setWidget(body)
        outer.addWidget(scroll, stretch=1)
        layout = QVBoxLayout(body)

        # feature toggles
        feature_box = self.feature_box = QGroupBox("Feature toggles")
        f_layout = QVBoxLayout(feature_box)
        self.feature_checks: dict[str, QCheckBox] = {}
        for key in ("auto_index", "duplicate_check", "sftp", "cd_burn"):
            cb = QCheckBox(key)
            cb.setEnabled(self.can_edit)
            self.feature_checks[key] = cb
            f_layout.addWidget(cb)
        layout.addWidget(feature_box)

        # separation strategies
        sep_box = self.sep_box = QGroupBox("Separation strategy")
        s_layout = QVBoxLayout(sep_box)
        self.strategy_checks: dict[str, QCheckBox] = {}
        for key in STRATEGIES:
            cb = QCheckBox(key)
            cb.setEnabled(self.can_edit)
            self.strategy_checks[key] = cb
            s_layout.addWidget(cb)
        fixed_row = QFormLayout()
        self.fixed_pages = QSpinBox()
        self.fixed_pages.setRange(0, 999)
        self.fixed_pages.setSpecialValueText("(off)")
        self.fixed_pages.setEnabled(self.can_edit)
        fixed_row.addRow("Fixed pages per document", self.fixed_pages)
        s_layout.addLayout(fixed_row)
        layout.addWidget(sep_box)

        # QC config
        qc_box = self.qc_box = QGroupBox("QC pipeline")
        q_layout = QFormLayout(qc_box)
        self.qc_preset = QComboBox()
        self.qc_preset.addItems(["Custom", *QC_PRESETS.keys()])
        self.qc_preset.setEnabled(self.can_edit)
        self.qc_preset.currentTextChanged.connect(self._apply_qc_preset)
        q_layout.addRow("Strictness preset", self.qc_preset)
        self.qc_enabled = QCheckBox("QC enabled")
        self.qc_enabled.setEnabled(self.can_edit)
        q_layout.addRow(self.qc_enabled)
        self.qc_stage_checks: dict[str, QCheckBox] = {}
        for stage in QC_STAGES:
            cb = QCheckBox(stage)
            cb.setEnabled(self.can_edit)
            self.qc_stage_checks[stage] = cb
            q_layout.addRow(cb)
        self.pass_score = QSpinBox()
        self.pass_score.setRange(0, 100)
        self.blur_warn = QSpinBox()
        self.blur_warn.setRange(0, 10000)
        self.blur_fail = QSpinBox()
        self.blur_fail.setRange(0, 10000)
        self.dpi_warn = QSpinBox()
        self.dpi_warn.setRange(0, 2400)
        self.dpi_fail = QSpinBox()
        self.dpi_fail.setRange(0, 2400)
        for w in (self.pass_score, self.blur_warn, self.blur_fail,
                  self.dpi_warn, self.dpi_fail):
            w.setEnabled(self.can_edit)
        q_layout.addRow("Pass score ≥", self.pass_score)
        q_layout.addRow("Blur WARN threshold", self.blur_warn)
        q_layout.addRow("Blur FAIL threshold", self.blur_fail)
        q_layout.addRow("DPI WARN threshold", self.dpi_warn)
        q_layout.addRow("DPI FAIL threshold", self.dpi_fail)
        layout.addWidget(qc_box)

        # scanner + export
        exp_box = self.exp_box = QGroupBox("Scanner & export")
        e_layout = QFormLayout(exp_box)
        self.dpi = QSpinBox()
        self.dpi.setRange(75, 1200)
        self.color = QComboBox()
        self.color.addItems(["bw", "grayscale", "color"])
        self.export_format = QComboBox()
        self.export_format.addItems(EXPORT_FORMATS)
        self.filename_template = QLineEdit()
        self.output_path = QLineEdit()
        self.delivery_path = QLineEdit()
        for w in (self.dpi, self.color, self.export_format,
                  self.filename_template, self.output_path,
                  self.delivery_path):
            w.setEnabled(self.can_edit)
        e_layout.addRow("Scan DPI", self.dpi)
        e_layout.addRow("Colour mode", self.color)
        e_layout.addRow("Export format", self.export_format)
        e_layout.addRow("Filename template", self.filename_template)
        e_layout.addRow("Export path", self.output_path)
        e_layout.addRow("Delivery folder", self.delivery_path)
        layout.addWidget(exp_box)

        # index zones (auto-index fields)
        zone_box = self.zone_box = QGroupBox("Index zones (auto-index fields)")
        z_layout = QVBoxLayout(zone_box)
        z_layout.addWidget(QLabel(
            "Each zone reads one field from the page. Zone-based: drag a "
            "fixed region per field. Full-page: OCR the whole page and "
            "match each field by a label-anchored regex — use this when "
            "field position varies between documents in this project."))
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Extraction mode"))
        self.ocr_mode = QComboBox()
        self.ocr_mode.addItems(["Zone-based (draw regions)",
                                "Full-page (label-anchored regex)"])
        self.ocr_mode.setEnabled(self.can_edit)
        self.ocr_mode.currentIndexChanged.connect(self._relabel_zones)
        mode_row.addWidget(self.ocr_mode)
        mode_row.addStretch(1)
        z_layout.addLayout(mode_row)
        self.zone_list = QListWidget()
        z_layout.addWidget(self.zone_list)
        zone_btns = QHBoxLayout()
        self.add_zone_btn = QPushButton("Add zone…")
        self.edit_zone_btn = QPushButton("Edit…")
        self.del_zone_btn = QPushButton("Remove")
        self.add_zone_btn.clicked.connect(self.add_zone)
        self.edit_zone_btn.clicked.connect(self.edit_zone)
        self.del_zone_btn.clicked.connect(
            lambda: self.zone_list.takeItem(self.zone_list.currentRow()))
        for b in (self.add_zone_btn, self.edit_zone_btn, self.del_zone_btn):
            b.setEnabled(self.can_edit)
            zone_btns.addWidget(b)
        z_layout.addLayout(zone_btns)
        layout.addWidget(zone_box)

        # SFTP delivery (Phase 3)
        sftp_box = self.sftp_box = QGroupBox("SFTP delivery (LAN)")
        sftp_layout = QFormLayout(sftp_box)
        self.sftp_enabled = QCheckBox("Enabled")
        self.sftp_enabled.setEnabled(self.can_edit)
        sftp_layout.addRow(self.sftp_enabled)
        self.sftp_host = QLineEdit()
        self.sftp_port = QSpinBox()
        self.sftp_port.setRange(1, 65535)
        self.sftp_port.setValue(22)
        self.sftp_user = QLineEdit()
        self.sftp_password = QLineEdit()
        self.sftp_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.sftp_key_file = QLineEdit()
        self.sftp_remote_path = QLineEdit()
        for w in (self.sftp_host, self.sftp_port, self.sftp_user,
                  self.sftp_password, self.sftp_key_file,
                  self.sftp_remote_path):
            w.setEnabled(self.can_edit)
        sftp_layout.addRow("Host", self.sftp_host)
        sftp_layout.addRow("Port", self.sftp_port)
        sftp_layout.addRow("Username", self.sftp_user)
        sftp_layout.addRow("Password", self.sftp_password)
        sftp_layout.addRow("Key file (optional)", self.sftp_key_file)
        sftp_layout.addRow("Remote path", self.sftp_remote_path)
        sftp_layout.addRow(QLabel("Tick Enabled to deliver via SFTP."))
        layout.addWidget(sftp_box)

        # CD/DVD delivery: local to this PC, like SFTP
        cd_box = self.cd_box = QGroupBox("CD/DVD delivery")
        cd_layout = QVBoxLayout(cd_box)
        self.cd_enabled = QCheckBox("Enabled")
        self.cd_enabled.setEnabled(self.can_edit)
        cd_layout.addWidget(self.cd_enabled)
        cd_layout.addWidget(QLabel("Tick Enabled to deliver to CD/DVD."))
        layout.addWidget(cd_box)

        # on an unmanaged project the shorthand toggles still drive the
        # checkboxes (the user may then change a checkbox independently)
        self.feature_checks["sftp"].toggled.connect(
            self.sftp_enabled.setChecked)
        self.feature_checks["cd_burn"].toggled.connect(
            self.cd_enabled.setChecked)

        # rules
        rule_box = self.rule_box = QGroupBox("Rules (IF / THEN)")
        r_layout = QVBoxLayout(rule_box)
        self.rule_list = QListWidget()
        r_layout.addWidget(self.rule_list)
        rule_btns = QHBoxLayout()
        self.add_rule_btn = QPushButton("Add rule…")
        self.edit_rule_btn = QPushButton("Edit…")
        self.del_rule_btn = QPushButton("Delete")
        self.add_rule_btn.clicked.connect(self.add_rule)
        self.edit_rule_btn.clicked.connect(self.edit_rule)
        self.del_rule_btn.clicked.connect(self.delete_rule)
        for b in (self.add_rule_btn, self.edit_rule_btn, self.del_rule_btn):
            b.setEnabled(self.can_edit)
            rule_btns.addWidget(b)
        r_layout.addLayout(rule_btns)
        layout.addWidget(rule_box)
        layout.addStretch(1)

        self.refresh_projects()

    # -- project selection --------------------------------------------------------
    def refresh_projects(self) -> None:
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for row in self.db.list_projects():
            self.project_combo.addItem(
                f"{row['name']} (v{row['version']})", row["project_id"])
        self.project_combo.blockSignals(False)
        if self.project_combo.count():
            self.project_combo.setCurrentIndex(0)
            self.load_selected()

    def load_selected(self) -> None:
        project_id = self.project_combo.currentData()
        if not project_id:
            return
        self.config = self.pm.load(project_id)
        self._bind_config()
        self._update_managed_state()

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
        for box in self._central_boxes():
            box.setEnabled(not managed)

    def _central_boxes(self) -> tuple[QGroupBox, ...]:
        return (self.feature_box, self.sep_box, self.qc_box, self.zone_box,
                self.rule_box)

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
        self.color.setCurrentText(cfg["scanner"]["color"])
        self.export_format.setCurrentText(cfg["export"]["format"])
        self.filename_template.setText(cfg["export"]["filename_template"])
        self.output_path.setText(cfg["export"]["output_path"])
        self.delivery_path.setText(
            cfg["delivery"]["local_folder"].get("path", ""))
        self.ocr_mode.blockSignals(True)
        self.ocr_mode.setCurrentText(
            "Full-page (label-anchored regex)"
            if cfg.get("ocr", {}).get("mode") == "full_page"
            else "Zone-based (draw regions)")
        self.ocr_mode.blockSignals(False)
        self.zone_list.clear()
        for zone in cfg.get("ocr", {}).get("zones", []):
            self._append_zone(zone)
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

    # -- editing --------------------------------------------------------------------
    def collect_config(self) -> dict:
        """Read every widget back into a config dict (pure; used by save
        and by tests)."""
        cfg = json.loads(json.dumps(self.config))  # deep copy
        cfg["features"].update({k: cb.isChecked()
                                for k, cb in self.feature_checks.items()})
        cfg["separation"]["strategy"] = [
            k for k, cb in self.strategy_checks.items() if cb.isChecked()]
        cfg["separation"]["fixed_pages"] = self.fixed_pages.value() or None
        cfg["qc"]["enabled"] = self.qc_enabled.isChecked()
        cfg["qc"]["stages"] = [s for s, cb in self.qc_stage_checks.items()
                               if cb.isChecked()]
        cfg["qc"]["pass_score"] = self.pass_score.value()
        cfg["qc"]["blur_warn_threshold"] = self.blur_warn.value()
        cfg["qc"]["blur_fail_threshold"] = self.blur_fail.value()
        cfg["qc"]["dpi_warn"] = self.dpi_warn.value()
        cfg["qc"]["dpi_fail"] = self.dpi_fail.value()
        cfg["scanner"]["dpi"] = self.dpi.value()
        cfg["scanner"]["color"] = self.color.currentText()
        cfg["export"]["format"] = self.export_format.currentText()
        cfg["export"]["filename_template"] = self.filename_template.text()
        cfg["export"]["output_path"] = self.output_path.text()
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
        cfg["rules"]["rules"] = [
            self.rule_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.rule_list.count())]
        return cfg

    # -- index zones ---------------------------------------------------------
    def _is_full_page_mode(self) -> bool:
        return self.ocr_mode.currentText().startswith("Full-page")

    @staticmethod
    def _zone_label(zone: dict, full_page: bool) -> str:
        # the project's extraction mode (not the zone dict's shape)
        # decides the label: a template-seeded zone before its first
        # Edit… may have no captured pixel region in either mode, and
        # showing "full-page" for a zone-mode project would be
        # misleading — those coordinates are page-layout specific and
        # don't transfer across projects, but the zone is still a
        # regular region zone once positioned.
        if full_page:
            return (f"{zone['name']} — full-page"
                    + (f" · /{zone['regex']}/" if zone.get("regex") else ""))
        if "x" not in zone:
            return (f"{zone['name']} — not yet positioned"
                    + (f" · /{zone['regex']}/" if zone.get("regex") else ""))
        return (
            f"{zone['name']} — "
            f"({zone.get('x', 0)}, {zone.get('y', 0)}) "
            f"{zone.get('w', 0)}×{zone.get('h', 0)}"
            + (f" · /{zone['regex']}/" if zone.get("regex") else ""))

    def _append_zone(self, zone: dict) -> None:
        item = QListWidgetItem(self._zone_label(zone, self._is_full_page_mode()))
        item.setData(Qt.ItemDataRole.UserRole, zone)
        self.zone_list.addItem(item)

    def _relabel_zones(self) -> None:
        """Flipping the extraction-mode combo changes what each already-
        listed zone's label should say (e.g. "(x, y) w×h" vs "full-page")
        even though the underlying zone dicts haven't changed — rewrite
        every item's text to match. _bind_config() blocks ocr_mode's
        signals while it sets the mode and separately repopulates
        zone_list from scratch, so this never runs during that initial
        load."""
        full_page = self._is_full_page_mode()
        for i in range(self.zone_list.count()):
            item = self.zone_list.item(i)
            zone = item.data(Qt.ItemDataRole.UserRole)
            item.setText(self._zone_label(zone, full_page))

    def add_zone(self) -> None:
        from ui.zone_dialog import ZoneDialog
        dlg = ZoneDialog(parent=self, full_page=self._is_full_page_mode())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            zone = dlg.build_zone()
            if zone:
                self._append_zone(zone)

    def edit_zone(self) -> None:
        item = self.zone_list.currentItem()
        if item is None:
            return
        from ui.zone_dialog import ZoneDialog
        dlg = ZoneDialog(
            item.data(Qt.ItemDataRole.UserRole), parent=self,
            full_page=self._is_full_page_mode())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            zone = dlg.build_zone()
            if zone:
                item.setData(Qt.ItemDataRole.UserRole, zone)
                item.setText(self._zone_label(zone, self._is_full_page_mode()))

    def _refuse_if_managed(self) -> bool:
        """True (after telling the user) when the shown project is managed
        by the central server: used for actions that would change
        central-owned content (a rollback restores an old central config)."""
        if not self._is_managed():
            return False
        QMessageBox.warning(
            self, "Managed centrally",
            "This project is managed by the central server; local changes "
            "would be overwritten at the next sync. Change it in the "
            "central dashboard instead.")
        return True

    def _write_failed(self, exc: OSError) -> None:
        """A project-file write failed (file locked by another program, disk
        error...): tell the user, log only the exception type, change nothing
        in the form. Qt slots must never let an exception escape."""
        log.error("project file write failed: %s", type(exc).__name__)
        QMessageBox.critical(self, "Could not save", WRITE_FAILED_TEXT)

    def _save_local_sections_only(self) -> None:
        """Managed project: persist ONLY scanner/export/delivery. The stored
        config is the base, so nothing edited in a central-owned part of the
        form (rules, ocr, qc, features, name...) can reach disk. The delivery
        on/off flags stay as stored: the shorthand feature toggles that
        collect_config copies them from belong to central.

        Narrow race: if a sync applies a new central version between the
        load and the save below, this save is based on the older central
        content and the next sync restores central's version. Local sections
        are never overwritten by a sync, so nothing local is lost."""
        stored = self.pm.load(self.config["project_id"])
        collected = self.collect_config()
        for key in LOCAL_ONLY_KEYS:
            stored[key] = collected[key]
        self.config = self.pm.save(stored)
        self._bind_config()
        self.refresh_projects_keep_selection()
        QMessageBox.information(
            self, "Saved",
            "Saved this PC's scanner / export / delivery settings. "
            "Everything else is managed centrally and was not changed.")

    def save(self) -> None:
        if self._is_managed():
            try:
                self._save_local_sections_only()
            except ProjectConfigError as e:
                QMessageBox.critical(self, "Invalid configuration", str(e))
            except OSError as e:
                self._write_failed(e)
            return
        try:
            cfg = self.collect_config()
            RuleEngine(cfg["rules"])          # validate DSL before saving
            self.config = self.pm.save(cfg)   # version++ & archive
            self._bind_config()
            self.refresh_projects_keep_selection()
            QMessageBox.information(
                self, "Saved", f"Saved as version {self.config['version']}.")
        except (ProjectConfigError, RuleSetError) as e:
            QMessageBox.critical(self, "Invalid configuration", str(e))
        except OSError as e:
            self._write_failed(e)

    def refresh_projects_keep_selection(self) -> None:
        current = self.project_combo.currentData()
        self.refresh_projects()
        idx = self.project_combo.findData(current)
        if idx >= 0:
            self.project_combo.setCurrentIndex(idx)

    def clone_project(self) -> None:
        if self.config is None:
            return
        name, ok = QInputDialog.getText(self, "Clone project",
                                        "New project name:")
        if ok and name.strip():
            try:
                self.pm.clone(self.config["project_id"], name.strip())
            except OSError as e:
                self._write_failed(e)
                return
            self.refresh_projects()

    def new_from_template(self) -> None:
        from core.template_manager import TemplateManager
        tm = TemplateManager(self.db)
        templates = tm.list_templates()
        if not templates:
            QMessageBox.information(self, "No templates",
                                    "No project templates available yet. "
                                    "Save a configured project as a "
                                    "template first, or check that "
                                    "built-in templates seeded correctly.")
            return
        labels = [f"{t['category']} — {t['name']}" for t in templates]
        label, ok = QInputDialog.getItem(
            self, "New from template", "Template:", labels, 0, False)
        if not ok:
            return
        template = templates[labels.index(label)]
        name, ok = QInputDialog.getText(self, "New project",
                                        "New project name:")
        if not (ok and name.strip()):
            return
        cfg = tm.build_project_config(template["template_id"])
        try:
            new_cfg = self.pm.create(name.strip(), config=cfg)
        except OSError as e:
            self._write_failed(e)
            return
        self.refresh_projects()
        idx = self.project_combo.findData(new_cfg["project_id"])
        if idx >= 0:
            self.project_combo.setCurrentIndex(idx)

    def save_as_template(self) -> None:
        if self.config is None:
            return
        name, ok = QInputDialog.getText(self, "Save as template",
                                        "Template name:")
        if not (ok and name.strip()):
            return
        category, ok = QInputDialog.getText(self, "Save as template",
                                            "Category:")
        if not (ok and category.strip()):
            return
        from core.template_manager import TemplateManager
        cfg = self.collect_config()
        TemplateManager(self.db).save_as_template(
            cfg, name.strip(), category.strip())
        QMessageBox.information(self, "Saved",
                                f"Saved as template “{name.strip()}”.")

    def rollback_dialog(self) -> None:
        if self.config is None or self._refuse_if_managed():
            return
        versions = self.pm.list_versions(self.config["project_id"])
        if not versions:
            QMessageBox.information(self, "Version history",
                                    "No archived versions yet.")
            return
        version, ok = QInputDialog.getItem(
            self, "Rollback", "Restore version:",
            [str(v) for v in versions], len(versions) - 1, False)
        if ok:
            try:
                restored = self.pm.rollback(self.config["project_id"],
                                            int(version))
            except OSError as e:
                self._write_failed(e)
                return
            self.config = restored
            self._bind_config()
            self.refresh_projects_keep_selection()

    def add_rule(self) -> None:
        dlg = RuleDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            existing = self.rule_list.count()
            rule = dlg.build_rule(f"r{existing + 1:03d}")
            if not rule["actions"] or "field" not in json.dumps(rule["condition"]):
                QMessageBox.warning(self, "Rule editor",
                                    "A rule needs at least one condition "
                                    "and one action.")
                return
            item = QListWidgetItem(f"{rule['id']} — {rule['name']}")
            item.setData(Qt.ItemDataRole.UserRole, rule)
            self.rule_list.addItem(item)

    def edit_rule(self) -> None:
        item = self.rule_list.currentItem()
        if item is None:
            return
        rule = item.data(Qt.ItemDataRole.UserRole)
        dlg = RuleDialog(rule, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            updated = dlg.build_rule(rule["id"])
            item.setData(Qt.ItemDataRole.UserRole, updated)
            item.setText(f"{updated['id']} — {updated['name']}")

    def delete_rule(self) -> None:
        self.rule_list.takeItem(self.rule_list.currentRow())
