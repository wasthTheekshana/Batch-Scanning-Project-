"""Page image viewer: zoom (wheel), pan, fit + thumbnail strip (HLD §16)."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QGraphicsPixmapItem, QGraphicsScene,
                             QGraphicsView, QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QPushButton, QWidget)


class ImageView(QGraphicsView):
    """Single-image zoom/pan canvas."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self._item = QGraphicsPixmapItem()
        self._scene.addItem(self._item)
        self.setScene(self._scene)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse)

    def set_image(self, path: str | Path) -> bool:
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return False
        self._item.setPixmap(pixmap)
        self._scene.setSceneRect(self._item.boundingRect())
        self.fit()
        return True

    def clear_image(self) -> None:
        """Show nothing (a page whose image is gone), not the previous page's."""
        self._item.setPixmap(QPixmap())

    def fit(self) -> None:
        if not self._item.pixmap().isNull():
            self.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event) -> None:
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        self.scale(factor, factor)


def document_pages(doc) -> list[dict]:
    """The pages of a document, in order: [{"no", "original", "working"}], from the paths the QC step recorded in
    qc_detail. Pages with no recorded image are left out."""
    import json
    try:
        pages = json.loads(doc["qc_detail"] or "{}").get("pages", {})
    except (TypeError, ValueError):
        return []
    out = []
    for no in sorted(pages, key=lambda k: int(k) if str(k).isdigit() else 0):
        info = pages[no]
        if isinstance(info, dict) and (info.get("original_path") or info.get("working_path")):
            out.append({"no": str(no), "original": info.get("original_path"), "working": info.get("working_path")})
    return out


class PageNav(QWidget):
    """Previous / Next and "Page 2 of 4", for a document that has more than one page. Hidden for a single page."""

    changed = pyqtSignal(int)          # the index (0-based) of the page now wanted

    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.prev_btn = QPushButton("◀ Previous page")
        self.next_btn = QPushButton("Next page ▶")
        self.label = QLabel("")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.prev_btn)
        row.addWidget(self.label, stretch=1)
        row.addWidget(self.next_btn)
        self.prev_btn.clicked.connect(lambda: self.go(self.index - 1))
        self.next_btn.clicked.connect(lambda: self.go(self.index + 1))
        self.count, self.index = 0, 0
        self.set_count(0)

    def set_count(self, count: int, index: int = 0) -> None:
        self.count, self.index = count, max(0, min(index, max(count - 1, 0)))
        self._refresh()

    def go(self, index: int) -> None:
        if 0 <= index < self.count and index != self.index:
            self.index = index
            self._refresh()
            self.changed.emit(index)

    def _refresh(self) -> None:
        self.setVisible(self.count > 1)
        self.label.setText(f"Page {self.index + 1} of {self.count}" if self.count else "")
        self.prev_btn.setEnabled(self.index > 0)
        self.next_btn.setEnabled(self.index < self.count - 1)


class ImageViewer(QWidget):
    """Thumbnail strip + main canvas."""

    page_selected = pyqtSignal(str)  # image path

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.thumbnails = QListWidget()
        self.thumbnails.setIconSize(QSize(96, 128))
        self.thumbnails.setMaximumWidth(140)
        self.view = ImageView()
        layout.addWidget(self.thumbnails)
        layout.addWidget(self.view, stretch=1)
        self.thumbnails.currentItemChanged.connect(self._on_thumb)

    def load_pages(self, paths: list[str]) -> None:
        self.thumbnails.clear()
        for i, path in enumerate(paths, start=1):
            item = QListWidgetItem(f"Page {i}")
            pm = QPixmap(str(path))
            if not pm.isNull():
                from PyQt6.QtGui import QIcon
                item.setIcon(QIcon(pm.scaled(
                    96, 128, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.thumbnails.addItem(item)
        if self.thumbnails.count():
            self.thumbnails.setCurrentRow(0)

    def _on_thumb(self, current, _previous) -> None:
        if current is not None:
            path = current.data(Qt.ItemDataRole.UserRole)
            self.view.set_image(path)
            self.page_selected.emit(path)
