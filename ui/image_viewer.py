"""Page image viewer: zoom (wheel), pan, fit + thumbnail strip (HLD §16)."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QGraphicsPixmapItem, QGraphicsScene,
                             QGraphicsView, QHBoxLayout, QListWidget,
                             QListWidgetItem, QWidget)


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

    def fit(self) -> None:
        if not self._item.pixmap().isNull():
            self.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event) -> None:
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        self.scale(factor, factor)


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
