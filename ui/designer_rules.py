"""The IF / THEN rules list.

A mixin of ui.workflow_designer.WorkflowDesigner: it relies on the widgets that class builds.
"""
from __future__ import annotations

import json

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QListWidgetItem, QMessageBox

from ui.rule_dialog import RuleDialog


class RulesMixin:
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
