"""Visual index-zone editor: load a sample page, drag the region a
field should be read from, and TEST it live — the dialog shows exactly
what OCR reads from the zone and what survives the clean-up regex, so
the admin adjusts until the extraction is right before saving
(HLD §6 same-day onboarding by non-technical admins)."""
from __future__ import annotations

import re

import cv2
import numpy as np
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog,
                             QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QSpinBox, QVBoxLayout)

from ui.page_editor import SelectableImageView
from ui.theme import mark


class ZoneDialog(QDialog):
    def __init__(self, zone: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Index zone")
        self.resize(900, 720)
        self._sample: np.ndarray | None = None
        self._ref = ((zone.get("ref_w"), zone.get("ref_h"))
                     if zone else (None, None))
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
            sb.valueChanged.connect(self._sync_overlay)
            coords.addWidget(QLabel(label))
            coords.addWidget(sb)
        if zone:
            self.x.setValue(zone["x"]); self.y.setValue(zone["y"])
            self.w.setValue(zone["w"]); self.h.setValue(zone["h"])
        form.addRow("Region (px)", coords)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        load_btn = QPushButton("Load sample page…")
        load_btn.clicked.connect(self.load_sample)
        btn_row.addWidget(load_btn)
        self.test_btn = QPushButton("Test zone (run OCR)")
        mark(self.test_btn, "primary")
        self.test_btn.clicked.connect(self.test_zone)
        btn_row.addWidget(self.test_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.hint = QLabel(
            "IMPORTANT: use a page from a real scan of this project so "
            "the zone position matches production pages. Drag the "
            "rectangle where the field appears, then Test.")
        mark(self.hint, "muted")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        self.result = QLabel("")
        self.result.setWordWrap(True)
        self.result.setStyleSheet(
            "background: #FFFFFF; border: 1px solid #D9DEE5; "
            "border-radius: 6px; padding: 8px;")
        layout.addWidget(self.result)

        self.view = SelectableImageView()
        self.view.region_selected.connect(self._on_region)
        layout.addWidget(self.view, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # -- sample & region -------------------------------------------------------
    def load_sample(self, path: str | None = None) -> None:
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, "Sample page", "",
                "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp)")
        if not path:
            return
        img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        if img is not None:
            self._sample = img
            self.view.show_array(img)
            self._sync_overlay()
            self.hint.setText("Drag the region where the field appears, "
                              "then click Test zone.")

    def _on_region(self, x: int, y: int, w: int, h: int) -> None:
        for sb, v in ((self.x, x), (self.y, y), (self.w, w), (self.h, h)):
            sb.blockSignals(True)
            sb.setValue(v)
            sb.blockSignals(False)
        self._sync_overlay()
        self.hint.setText(f"Region set: {w}×{h} px at ({x}, {y}). "
                          "Click Test zone to see what it reads.")

    def _sync_overlay(self) -> None:
        if self._sample is not None and self.w.value() and self.h.value():
            self.view.show_overlay_rect(self.x.value(), self.y.value(),
                                        self.w.value(), self.h.value())

    # -- live testing ------------------------------------------------------------
    def test_zone(self) -> None:
        """Run real OCR on the configured region of the loaded sample and
        show the raw read + the regex result."""
        if self._sample is None:
            self.result.setText("Load a sample page first.")
            return
        if not self.w.value() or not self.h.value():
            self.result.setText("Drag a region on the sample first.")
            return
        h, w = self._sample.shape[:2]
        x0 = max(0, min(self.x.value(), w - 1))
        y0 = max(0, min(self.y.value(), h - 1))
        x1 = max(x0 + 1, min(x0 + self.w.value(), w))
        y1 = max(y0 + 1, min(y0 + self.h.value(), h))
        region = self._sample[y0:y1, x0:x1]
        try:
            from ocr.tesseract_engine import TesseractNotFound, ocr_page
            read = ocr_page(region)
        except TesseractNotFound:
            self.result.setText("Tesseract OCR is not installed on this "
                                "machine — cannot test here, but the zone "
                                "will work on machines with OCR.")
            return
        except Exception as e:
            self.result.setText(f"OCR failed: {e}")
            return

        raw = read.text.strip() or "(nothing readable in this region)"
        lines = [f"OCR read: “{raw}”  (confidence "
                 f"{read.confidence:.0%})"]
        pattern = self.regex.text().strip()
        if pattern:
            try:
                m = re.search(pattern, read.text)
            except re.error as e:
                lines.append(f"Regex is invalid: {e}")
                m = None
            if m:
                value = m.group(1) if m.lastindex else m.group(0)
                lines.append(f"After regex → “{value}”  ✓ this is what "
                             "will be stored")
            elif pattern:
                lines.append("After regex → NO MATCH — the field would be "
                             "empty. Adjust the regex or the region.")
        else:
            lines.append(f"No regex — stored as-is: “{raw}”")
        self.result.setText("\n".join(lines))

    def build_zone(self) -> dict | None:
        name = self.name.text().strip()
        if not name or self.w.value() == 0 or self.h.value() == 0:
            return None
        zone = {"name": name, "x": self.x.value(), "y": self.y.value(),
                "w": self.w.value(), "h": self.h.value()}
        if self._sample is not None:
            # remember the sample's dimensions so the zone scales to
            # pages of any resolution (resolution-independent zones)
            zone["ref_h"], zone["ref_w"] = self._sample.shape[:2]
        elif self._ref[0] and self._ref[1]:
            zone["ref_w"], zone["ref_h"] = self._ref  # keep on re-edit
        if self.regex.text().strip():
            zone["regex"] = self.regex.text().strip()
        return zone
