"""Visual index-zone editor: load a sample page and drag the region a
field should be read from — no JSON editing required (HLD §6 same-day
onboarding by non-technical admins)."""
from __future__ import annotations

import cv2
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog,
                             QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QSpinBox, QVBoxLayout)

from ui.page_editor import SelectableImageView
from ui.theme import mark


class ZoneDialog(QDialog):
    def __init__(self, zone: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Index zone")
        self.resize(900, 700)
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.name = QLineEdit(zone["name"] if zone else "")
        self.name.setPlaceholderText("e.g. invoice_no")
        form.addRow("Field name", self.name)
        self.regex = QLineEdit(zone.get("regex", "") if zone else "")
        self.regex.setPlaceholderText(r"optional, e.g. (INV-\d+)")
        form.addRow("Clean-up regex", self.regex)
        coords = QHBoxLayout()
        self.x = QSpinBox(); self.y = QSpinBox()
        self.w = QSpinBox(); self.h = QSpinBox()
        for sb, label in ((self.x, "x"), (self.y, "y"),
                          (self.w, "w"), (self.h, "h")):
            sb.setRange(0, 20000)
            coords.addWidget(QLabel(label))
            coords.addWidget(sb)
        if zone:
            self.x.setValue(zone["x"]); self.y.setValue(zone["y"])
            self.w.setValue(zone["w"]); self.h.setValue(zone["h"])
        form.addRow("Region (px)", coords)
        layout.addLayout(form)

        load_btn = QPushButton("Load sample page…")
        load_btn.clicked.connect(self.load_sample)
        layout.addWidget(load_btn)
        self.hint = QLabel("Load a sample scanned page, then drag the "
                           "rectangle where this field appears.")
        mark(self.hint, "muted")
        layout.addWidget(self.hint)
        self.view = SelectableImageView()
        self.view.region_selected.connect(self._on_region)
        layout.addWidget(self.view, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def load_sample(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Sample page", "",
            "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp)")
        if not path:
            return
        img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        if img is not None:
            self.view.show_array(img)
            self.hint.setText("Drag the region where the field appears.")

    def _on_region(self, x: int, y: int, w: int, h: int) -> None:
        self.x.setValue(x); self.y.setValue(y)
        self.w.setValue(w); self.h.setValue(h)
        self.hint.setText(f"Region set: {w}×{h} px at ({x}, {y}).")

    def build_zone(self) -> dict | None:
        name = self.name.text().strip()
        if not name or self.w.value() == 0 or self.h.value() == 0:
            return None
        zone = {"name": name, "x": self.x.value(), "y": self.y.value(),
                "w": self.w.value(), "h": self.h.value()}
        if self.regex.text().strip():
            zone["regex"] = self.regex.text().strip()
        return zone
