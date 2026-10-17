"""The dialog for one rule of a project: its checks and its actions (Projects tab, Rules)."""
from __future__ import annotations

import json

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                             QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                             QPushButton, QVBoxLayout)

OPERATORS = ["eq", "neq", "contains", "not_contains", "is_empty",
             "is_not_empty", "gt", "lt", "gte", "lte", "matches_regex"]
ACTIONS = ["set_folder", "set_filename", "set_validation_status",
           "add_flag", "set_metadata_field", "trigger_delivery",
           "skip_stage"]


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
