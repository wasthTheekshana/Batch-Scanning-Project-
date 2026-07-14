"""Job monitor — live pipeline progress table fed by the event bus
(HLD §16)."""
from __future__ import annotations

from datetime import datetime

from PyQt6.QtWidgets import (QLabel, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

MAX_ROWS = 500


class JobMonitor(QWidget):
    def __init__(self, bus, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.status = QLabel("Pipeline idle")
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Time", "Stage", "Status", "Detail"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)
        bus.job_progress.connect(self.on_event)

    def on_event(self, event: dict) -> None:
        row = 0
        self.table.insertRow(row)
        detail = {k: v for k, v in event.items()
                  if k not in ("stage", "status")}
        cells = [datetime.now().strftime("%H:%M:%S"),
                 str(event.get("stage", "")),
                 str(event.get("status", "")),
                 ", ".join(f"{k}={v}" for k, v in detail.items())]
        for col, text in enumerate(cells):
            self.table.setItem(row, col, QTableWidgetItem(text))
        while self.table.rowCount() > MAX_ROWS:
            self.table.removeRow(self.table.rowCount() - 1)
        self.status.setText(
            f"Last event: {event.get('stage')} · {event.get('status')}")
