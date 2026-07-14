"""Scan wizard — multi-step job setup (HLD §16).

Pages: 1) select project → 2) select source & files → 3) confirm.
On finish it emits batch_ready(batch_id, list[ScanJob]) for MainWindow
to feed into the pipeline.
"""
from __future__ import annotations

import uuid

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QLabel, QListWidget,
                             QPushButton, QVBoxLayout, QWizard, QWizardPage)

from core.models import ScanJob
from scanning.file_import import import_files


class ProjectPage(QWizardPage):
    def __init__(self, projects: list[dict]):
        super().__init__()
        self.setTitle("Select project")
        self.projects = projects
        layout = QVBoxLayout(self)
        self.combo = QComboBox()
        for p in projects:
            self.combo.addItem(p["name"], p["project_id"])
        layout.addWidget(QLabel("Project profile for this batch:"))
        layout.addWidget(self.combo)

    def selected_project_id(self) -> str | None:
        return self.combo.currentData()


class SourcePage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Select input source")
        layout = QVBoxLayout(self)
        self.source = QComboBox()
        self.source.addItem("File import (PDF/TIFF/JPG/PNG)", "file")
        self.source.addItem("TWAIN scanner", "twain")
        self.source.addItem("WIA scanner", "wia")
        layout.addWidget(self.source)
        self.pick_button = QPushButton("Add files…")
        self.pick_button.clicked.connect(self.pick_files)
        layout.addWidget(self.pick_button)
        self.file_list = QListWidget()
        layout.addWidget(self.file_list)

    def pick_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select documents", "",
            "Documents (*.pdf *.tif *.tiff *.jpg *.jpeg *.png *.bmp)")
        for p in paths:
            self.file_list.addItem(p)

    def selected_files(self) -> list[str]:
        return [self.file_list.item(i).text()
                for i in range(self.file_list.count())]

    def isComplete(self) -> bool:  # noqa: N802 (Qt override)
        return True


class ConfirmPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Confirm & start")
        layout = QVBoxLayout(self)
        self.summary = QLabel("")
        layout.addWidget(self.summary)


class ScanWizard(QWizard):
    batch_ready = pyqtSignal(str, list)  # batch_id, [ScanJob]

    def __init__(self, projects: list[dict], staging_dir, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Scan Batch")
        self.staging_dir = staging_dir
        self.project_page = ProjectPage(projects)
        self.source_page = SourcePage()
        self.confirm_page = ConfirmPage()
        self.addPage(self.project_page)
        self.addPage(self.source_page)
        self.addPage(self.confirm_page)
        self.currentIdChanged.connect(self._refresh_summary)

    def _refresh_summary(self, _page_id: int) -> None:
        if self.currentPage() is self.confirm_page:
            files = self.source_page.selected_files()
            self.confirm_page.summary.setText(
                f"Project: {self.project_page.combo.currentText()}\n"
                f"Source: {self.source_page.source.currentText()}\n"
                f"Files: {len(files)}")

    def accept(self) -> None:
        project_id = self.project_page.selected_project_id()
        files = self.source_page.selected_files()
        if project_id and files:
            batch_id = str(uuid.uuid4())
            jobs: list[ScanJob] = import_files(
                files, batch_id, project_id, self.staging_dir)
            if jobs:
                self.batch_ready.emit(batch_id, jobs)
        super().accept()
