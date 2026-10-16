"""Scan wizard — multi-step job setup (HLD §16).

Pages: 1) select project → 2) select source & files → 3) confirm.
On finish it emits batch_ready(batch_id, list[ScanJob]) for MainWindow
to feed into the pipeline.
"""
from __future__ import annotations

import inspect
import logging
import shutil
import uuid
from pathlib import Path

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QLabel, QListWidget,
                             QMessageBox, QPushButton, QVBoxLayout, QWizard,
                             QWizardPage)

from core.models import ScanJob
from scanning.base import ScannerUnavailable
from scanning.file_import import import_files

log = logging.getLogger(__name__)


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


class ScanWorker(QThread):
    """Runs one feeder scan off the UI thread; emits each page as it lands."""
    page = pyqtSignal(bytes, int)
    done = pyqtSignal(int)
    failed = pyqtSignal(str)
    notice = pyqtSignal(str)

    def __init__(self, source, device: str, settings: dict, parent=None):
        super().__init__(parent)
        self.source = source
        self.device = device
        self.settings = settings

    def run(self) -> None:
        com = None
        try:
            import pythoncom        # WIA is COM: each thread must initialise it
            pythoncom.CoInitialize()
            com = pythoncom
        except ImportError:
            pass
        try:
            self.source.connect(self.device)
            kwargs = {}
            if "on_page" in inspect.signature(self.source.acquire).parameters:
                kwargs["on_page"] = lambda data, n: self.page.emit(data, n)
            pages = self.source.acquire(
                self.settings["dpi"], self.settings["color"],
                self.settings["duplex"], **kwargs)
            self.done.emit(len(pages))
            if self.settings["duplex"] and getattr(self.source, "duplex_applied", None) is False:
                self.notice.emit(
                    "This scanner would not scan both sides, so only the front of "
                    "each sheet was scanned. Switch duplex off for this project, or "
                    "check the scanner's two-sided setting.")
        except ScannerUnavailable as e:
            self.failed.emit(str(e))
        except Exception as e:
            log.exception("scan failed")
            self.failed.emit(f"The scan failed ({type(e).__name__}).")
        finally:
            try:
                self.source.disconnect()
            finally:
                # release the scanner's COM objects NOW: until they are
                # collected the device can still look busy to the next scan
                import gc
                gc.collect()
                if com is not None:
                    com.CoUninitialize()


def _default_devices() -> dict[str, list[str]]:
    from scanning.sources import available_sources
    return available_sources()


def _default_source(name: str):
    from scanning.sources import SOURCE_REGISTRY
    return SOURCE_REGISTRY[name]()


class SourcePage(QWizardPage):
    def __init__(self, staging_dir=None, devices: dict | None = None,
                 source_factory=None, settings=None):
        super().__init__()
        self.setTitle("Select input source")
        self._staging_dir = Path(staging_dir) if staging_dir else None
        self._source_factory = source_factory or _default_source
        self._settings = settings or (lambda: {"dpi": 300, "color": "grayscale",
                                               "duplex": True})
        self._worker: ScanWorker | None = None
        self.scan_dir: Path | None = None
        layout = QVBoxLayout(self)
        self.source = QComboBox()
        self.source.addItem("File import (PDF/TIFF/JPG/PNG)", None)
        for name, names in (devices or {}).items():
            for device in names:
                self.source.addItem(f"Scanner: {device} ({name.upper()})",
                                    (name, device))
        self.source.currentIndexChanged.connect(self._source_changed)
        layout.addWidget(self.source)
        self.pick_button = QPushButton("Add files…")
        self.pick_button.clicked.connect(self.pick_files)
        layout.addWidget(self.pick_button)
        self.scan_button = QPushButton("Scan from document feeder")
        self.scan_button.clicked.connect(self.start_scan)
        layout.addWidget(self.scan_button)
        self.status = QLabel("")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.file_list = QListWidget()
        layout.addWidget(self.file_list)
        self._source_changed()

    @property
    def scanning(self) -> bool:
        return self._worker is not None

    def _source_changed(self, _index: int = 0) -> None:
        scanner = self.source.currentData() is not None
        self.pick_button.setVisible(not scanner)
        self.scan_button.setVisible(scanner)
        self.status.setText("Load the pages in the document feeder, then "
                            "press Scan." if scanner else "")

    def pick_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select documents", "",
            "Documents (*.pdf *.tif *.tiff *.jpg *.jpeg *.png *.bmp)")
        for p in paths:
            self.file_list.addItem(p)

    def start_scan(self) -> None:
        choice = self.source.currentData()
        if choice is None or self.scanning:
            return
        name, device = choice
        if self._staging_dir is None:
            self.status.setText("No staging folder is configured.")
            return
        if self.scan_dir is None:
            self.scan_dir = self._staging_dir / f"scan_{uuid.uuid4().hex[:12]}"
        self.scan_dir.mkdir(parents=True, exist_ok=True)
        self._worker = ScanWorker(self._source_factory(name), device,
                                  self._settings(), parent=self)
        self._worker.page.connect(self._on_page)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.notice.connect(self._on_notice)
        self._worker.finished.connect(self._worker_finished)
        self.scan_button.setEnabled(False)
        self.source.setEnabled(False)
        self.status.setText("Scanning…")
        self.completeChanged.emit()
        self._worker.start()

    def _on_page(self, data: bytes, number: int) -> None:
        path = self.scan_dir / f"page_{self.file_list.count() + 1:04d}.png"
        path.write_bytes(data)
        self.file_list.addItem(str(path))
        self.status.setText(f"Scanned {number} page(s)…")

    def _on_done(self, count: int) -> None:
        self.status.setText(f"Scan complete: {count} page(s) added. Load more "
                            "and press Scan again, or continue.")

    def _on_notice(self, message: str) -> None:
        self.status.setText(self.status.text() + " " + message)

    def _on_failed(self, message: str) -> None:
        added = self.file_list.count()
        self.status.setText(message + (f" {added} page(s) already scanned "
                                       "were kept." if added else ""))

    def _worker_finished(self) -> None:
        self._worker.deleteLater()
        self._worker = None
        self.scan_button.setEnabled(True)
        self.source.setEnabled(True)
        self.completeChanged.emit()

    def selected_files(self) -> list[str]:
        return [self.file_list.item(i).text()
                for i in range(self.file_list.count())]

    def isComplete(self) -> bool:  # noqa: N802 (Qt override)
        return not self.scanning


class ConfirmPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Confirm & start")
        layout = QVBoxLayout(self)
        self.summary = QLabel("")
        layout.addWidget(self.summary)


class ScanWizard(QWizard):
    batch_ready = pyqtSignal(str, list)  # batch_id, [ScanJob]

    def __init__(self, projects: list[dict], staging_dir, parent=None,
                 devices=None, source_factory=None):
        super().__init__(parent)
        self.setWindowTitle("New Scan Batch")
        self.staging_dir = staging_dir
        self.project_page = ProjectPage(projects)
        if devices is None:
            try:
                devices = _default_devices()
            except Exception:
                log.exception("scanner detection failed")
                devices = {}
        self.source_page = SourcePage(
            staging_dir, devices, source_factory,
            settings=lambda: self._scanner_settings(projects))
        self.confirm_page = ConfirmPage()
        self.addPage(self.project_page)
        self.addPage(self.source_page)
        self.addPage(self.confirm_page)
        self.currentIdChanged.connect(self._refresh_summary)

    def _scanner_settings(self, projects: list[dict]) -> dict:
        """dpi/colour/duplex of the project chosen on page 1."""
        chosen = self.project_page.selected_project_id()
        cfg = next((p.get("config") or {} for p in projects
                    if p["project_id"] == chosen), {})
        scanner = cfg.get("scanner") or {}
        return {"dpi": int(scanner.get("dpi", 300)),
                "color": scanner.get("color", "grayscale"),
                "duplex": bool(scanner.get("duplex", True))}

    def done(self, result: int) -> None:
        if self.source_page.scanning:
            return          # never tear the wizard down under a running scan
        super().done(result)

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
        if not project_id or not files:
            QMessageBox.warning(
                self, "Nothing to scan",
                "Select a project and add at least one file before starting.")
            return  # keep the wizard open
        batch_id = str(uuid.uuid4())
        jobs: list[ScanJob] = import_files(
            files, batch_id, project_id, self.staging_dir)
        if not jobs:
            QMessageBox.critical(
                self, "Import failed",
                "No pages could be imported from the selected files.\n\n"
                "Check the Job Monitor log — common causes: unsupported "
                "file type, or PDF support (PyMuPDF) missing from this "
                "installation.")
            return  # keep the wizard open so the operator can retry
        self.batch_ready.emit(batch_id, jobs)
        if self.source_page.scan_dir is not None:     # pages were copied
            shutil.rmtree(self.source_page.scan_dir, ignore_errors=True)
        super().accept()
