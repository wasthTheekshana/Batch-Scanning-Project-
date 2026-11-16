"""The index zones and the document types lists.

A mixin of ui.workflow_designer.WorkflowDesigner: it relies on the widgets that class builds.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QListWidgetItem


class ZonesAndTypesMixin:
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

    @staticmethod
    def _type_label(doc_type: dict) -> str:
        count = len(doc_type.get("zones") or [])
        return (f"{doc_type['name']} — recognised by /{doc_type.get('regex', '')}/"
                f" · {count} field{'s' if count != 1 else ''}")

    def _append_type(self, doc_type: dict) -> None:
        item = QListWidgetItem(self._type_label(doc_type))
        item.setData(Qt.ItemDataRole.UserRole, doc_type)
        self.type_list.addItem(item)

    def _type_names(self, skip_row: int = -1) -> tuple:
        return tuple(self.type_list.item(i).data(Qt.ItemDataRole.UserRole)["name"]
                     for i in range(self.type_list.count()) if i != skip_row)

    def add_type(self) -> None:
        from ui.document_type_dialog import DocumentTypeDialog
        dlg = DocumentTypeDialog(parent=self, full_page=self._is_full_page_mode(),
                                 taken_names=self._type_names())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._append_type(dlg.build_type())

    def edit_type(self) -> None:
        item = self.type_list.currentItem()
        if item is None:
            return
        from ui.document_type_dialog import DocumentTypeDialog
        dlg = DocumentTypeDialog(
            item.data(Qt.ItemDataRole.UserRole), parent=self,
            full_page=self._is_full_page_mode(),
            taken_names=self._type_names(self.type_list.currentRow()))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            doc_type = dlg.build_type()
            item.setData(Qt.ItemDataRole.UserRole, doc_type)
            item.setText(self._type_label(doc_type))
