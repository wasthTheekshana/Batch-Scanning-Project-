"""Print barcode cover sheets / sticker labels for a list of customer IDs."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFormLayout,
                             QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                             QPlainTextEdit, QPushButton, QSpinBox,
                             QVBoxLayout)

from scanning.cover_sheets import (MAX_COPIES, CoverSheetError, parse_ids,
                                   sequence_ids, write_cover_sheets)
from ui.theme import fit_to_screen, mark


class CoverSheetDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Print cover sheets / barcode labels")
        fit_to_screen(self, 560, 560)
        self.last_path = None
        layout = QVBoxLayout(self)
        intro = QLabel(
            "One barcode per customer. Put a cover sheet on top of each "
            "customer's papers, or stick a label on the first page of a "
            "document. When scanned, the barcode starts a new PDF and "
            "becomes its barcode field.")
        intro.setWordWrap(True)
        mark(intro, "muted")
        layout.addWidget(intro)

        seq = QFormLayout()
        seq.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.prefix = QLineEdit()
        self.prefix.setPlaceholderText("e.g. CIF-")
        self.start = QSpinBox()
        self.start.setRange(0, 2_000_000_000)
        self.count = QSpinBox()
        self.count.setRange(1, 5000)
        self.count.setValue(10)
        self.width = QSpinBox()
        self.width.setRange(0, 12)
        self.width.setSpecialValueText("(no padding)")
        seq.addRow("Prefix", self.prefix)
        seq.addRow("Start number", self.start)
        seq.addRow("How many", self.count)
        seq.addRow("Digits (zero-padded)", self.width)
        layout.addLayout(seq)
        self.add_series_btn = QPushButton("Add this number series to the list")
        self.add_series_btn.clicked.connect(self.add_series)
        layout.addWidget(self.add_series_btn)

        ids_label = QLabel("IDs to print (one per line, or paste a "
                           "column from Excel)")
        ids_label.setWordWrap(True)
        layout.addWidget(ids_label)
        self.ids = QPlainTextEdit()
        self.ids.textChanged.connect(self._recount)
        layout.addWidget(self.ids, stretch=1)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.summary)

        opts = QFormLayout()
        opts.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.layout_combo = QComboBox()
        self.layout_combo.addItem("Cover sheets - one full page per ID", "cover")
        self.layout_combo.addItem("Small labels - 30 stickers per sheet", "labels")
        self.layout_combo.currentIndexChanged.connect(self._layout_changed)
        self.copies = QSpinBox()
        self.copies.setRange(1, MAX_COPIES)
        self.copies.setEnabled(False)
        opts.addRow("Print as", self.layout_combo)
        opts.addRow("Copies of each label", self.copies)
        layout.addLayout(opts)

        row = QHBoxLayout()
        self.create_btn = QPushButton("Create PDF…")
        mark(self.create_btn, "primary")
        self.create_btn.clicked.connect(self.create_pdf)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        row.addStretch(1)
        row.addWidget(self.create_btn)
        row.addWidget(close)
        layout.addLayout(row)
        self._recount()

    # -- input -------------------------------------------------------------------
    def _layout_changed(self) -> None:
        self.copies.setEnabled(self.layout_combo.currentData() == "labels")

    def add_series(self) -> None:
        try:
            added = sequence_ids(self.prefix.text(), self.start.value(),
                                 self.count.value(), self.width.value())
        except CoverSheetError as e:
            self.summary.setText(str(e))
            return
        existing = self.ids.toPlainText().rstrip("\n")
        self.ids.setPlainText((existing + "\n" if existing else "")
                              + "\n".join(added))

    def _recount(self) -> None:
        ids, rejected = parse_ids(self.ids.toPlainText())
        text = f"{len(ids)} unique ID(s) to print."
        if rejected:
            text += (f" {len(rejected)} entr{'y' if len(rejected) == 1 else 'ies'} "
                     "skipped (letters, digits and symbols only, up to 40 "
                     "characters).")
        self.summary.setText(text)
        self.create_btn.setEnabled(bool(ids))

    # -- output --------------------------------------------------------------------
    def create_pdf(self) -> None:
        ids, _rejected = parse_ids(self.ids.toPlainText())
        path, _ = QFileDialog.getSaveFileName(
            self, "Save cover sheets", "cover_sheets.pdf", "PDF (*.pdf)")
        if not path:
            return
        try:
            out = write_cover_sheets(
                ids, path, layout=self.layout_combo.currentData(),
                copies=self.copies.value())
        except (CoverSheetError, OSError) as e:
            QMessageBox.warning(self, "Could not create the PDF", str(e))
            return
        self.last_path = out
        self.summary.setText(f"Saved {out}")
