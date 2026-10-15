"""Manual page editor (QC review → Edit page).

The operator can fix what auto-QC got wrong: rotate (90° steps, fine
angle, or auto deskew), crop away unnecessary edges (selection or
auto), white-out black space (selection or automatic border removal),
and push enhancement further with brightness/contrast on top of the
auto profile. Every applied operation is logged to the audit trail;
the original scan file is never modified — edits go to the working
copy that feeds export.
"""
from __future__ import annotations

import cv2
import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (QComboBox, QDialog, QDoubleSpinBox, QGroupBox,
                             QHBoxLayout, QLabel, QPushButton, QSlider,
                             QVBoxLayout)

from imaging.deskew import deskew
from imaging.crop import auto_crop
from imaging.enhance import apply_profile
from imaging.manual_edit import (adjust_brightness_contrast, crop_rect,
                                 remove_black_borders, rotate90, rotate_fine,
                                 whiteout_rect)
from ui.image_viewer import ImageView
from ui.theme import fit_to_screen

UNDO_DEPTH = 10


def np_to_pixmap(img: np.ndarray) -> QPixmap:
    # tobytes() re-packs sliced (non-contiguous) arrays such as crop
    # results into a contiguous buffer with the stride QImage expects
    if img.ndim == 2:
        h, w = img.shape
        qimg = QImage(img.tobytes(), w, h, w, QImage.Format.Format_Grayscale8)
    else:
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        h, w, _ = rgb.shape
        qimg = QImage(rgb.tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())


class SelectableImageView(ImageView):
    """ImageView with rubber-band selection reported in image pixels."""

    region_selected = pyqtSignal(int, int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragMode(self.DragMode.RubberBandDrag)
        self.rubberBandChanged.connect(self._on_rubber_band)
        self._pending: tuple[int, int, int, int] | None = None

    def _on_rubber_band(self, viewport_rect, from_scene, to_scene) -> None:
        if not viewport_rect.isNull():
            x0, y0 = from_scene.x(), from_scene.y()
            x1, y1 = to_scene.x(), to_scene.y()
            self._pending = (int(min(x0, x1)), int(min(y0, y1)),
                             int(abs(x1 - x0)), int(abs(y1 - y0)))
        elif self._pending is not None:
            x, y, w, h = self._pending
            self._pending = None
            if w > 2 and h > 2:
                self.region_selected.emit(x, y, w, h)

    def show_array(self, img: np.ndarray) -> None:
        self._item.setPixmap(np_to_pixmap(img))
        self._scene.setSceneRect(self._item.boundingRect())
        self.fit()

    def show_overlay_rect(self, x: int, y: int, w: int, h: int) -> None:
        """Draw/refresh a persistent rectangle marking a region
        (e.g. the currently configured index zone)."""
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QColor, QPen
        if getattr(self, "_overlay", None) is None:
            pen = QPen(QColor("#0E7C66"))
            pen.setWidth(3)
            pen.setCosmetic(True)
            self._overlay = self._scene.addRect(QRectF(), pen)
            self._overlay.setZValue(10)
        self._overlay.setRect(QRectF(x, y, w, h))


class PageEditDialog(QDialog):
    """Edit one page's working image; ops are audited on save."""

    saved = pyqtSignal(str, int)  # document_id, page_no

    def __init__(self, document_id: str, pages: dict[str, dict],
                 audit=None, username: str = "unknown", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit page")
        fit_to_screen(self, 1000, 760)
        self.document_id = document_id
        self.pages = pages           # page_no -> {original_path, working_path}
        self.audit = audit
        self.username = username
        self.img: np.ndarray | None = None
        self.ops: list[dict] = []
        self._undo: list[np.ndarray] = []
        self._selection: tuple[int, int, int, int] | None = None

        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Page:"))
        self.page_combo = QComboBox()
        for page_no in sorted(pages, key=lambda k: int(k)):
            self.page_combo.addItem(f"Page {page_no}", page_no)
        self.page_combo.currentIndexChanged.connect(self._load_page)
        top.addWidget(self.page_combo)
        self.status = QLabel("Drag on the image to select a region.")
        top.addWidget(self.status, stretch=1)
        layout.addLayout(top)

        self.view = SelectableImageView()
        self.view.region_selected.connect(self._on_selection)
        layout.addWidget(self.view, stretch=1)

        tools = QHBoxLayout()

        rot_box = QGroupBox("Rotate")
        rot = QHBoxLayout(rot_box)
        for label, fn in (("90° ccw", lambda: self._apply(
                              "rotate90", turns=-1)),
                          ("90° cw", lambda: self._apply(
                              "rotate90", turns=1)),
                          ("180°", lambda: self._apply("rotate90", turns=2)),
                          ("Auto deskew", self._auto_deskew)):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, f=fn: f())
            rot.addWidget(b)
        self.angle = QDoubleSpinBox()
        self.angle.setRange(-15.0, 15.0)
        self.angle.setSingleStep(0.5)
        self.angle.setSuffix("°")
        rot.addWidget(self.angle)
        apply_angle = QPushButton("Apply angle")
        apply_angle.clicked.connect(
            lambda: self._apply("rotate_fine", angle=self.angle.value()))
        rot.addWidget(apply_angle)
        tools.addWidget(rot_box)

        crop_box = QGroupBox("Edges / black space")
        crop = QHBoxLayout(crop_box)
        for label, fn in (("Crop to selection", self._crop_selection),
                          ("Auto crop edges", self._auto_crop),
                          ("White-out selection", self._whiteout_selection),
                          ("Auto remove black borders",
                           self._auto_black_borders)):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, f=fn: f())
            crop.addWidget(b)
        tools.addWidget(crop_box)
        layout.addLayout(tools)

        enh_box = QGroupBox("Enhance (on top of auto profile)")
        enh = QHBoxLayout(enh_box)
        enh.addWidget(QLabel("Brightness"))
        self.brightness = QSlider(Qt.Orientation.Horizontal)
        self.brightness.setRange(-100, 100)
        self.brightness.setValue(0)
        enh.addWidget(self.brightness)
        enh.addWidget(QLabel("Contrast"))
        self.contrast = QSlider(Qt.Orientation.Horizontal)
        self.contrast.setRange(50, 200)
        self.contrast.setValue(100)
        enh.addWidget(self.contrast)
        apply_enh = QPushButton("Apply enhance")
        apply_enh.clicked.connect(self._apply_enhance)
        enh.addWidget(apply_enh)
        auto_enh = QPushButton("Auto enhance")
        auto_enh.clicked.connect(self._auto_enhance)
        enh.addWidget(auto_enh)
        layout.addWidget(enh_box)

        bottom = QHBoxLayout()
        undo = QPushButton("Undo")
        undo.clicked.connect(self._undo_last)
        reset = QPushButton("Reset to original scan")
        reset.clicked.connect(self._reset)
        self.save_btn = QPushButton("Save changes")
        self.save_btn.clicked.connect(self._save)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        bottom.addWidget(undo)
        bottom.addWidget(reset)
        bottom.addStretch(1)
        bottom.addWidget(cancel)
        bottom.addWidget(self.save_btn)
        from ui.theme import mark
        mark(self.save_btn, "primary")
        layout.addLayout(bottom)

        self._load_page()

    # -- state ------------------------------------------------------------------
    def current_page_no(self) -> str:
        return self.page_combo.currentData()

    def _load_page(self) -> None:
        info = self.pages[self.current_page_no()]
        self.img = cv2.imread(info["working_path"], cv2.IMREAD_UNCHANGED)
        self.ops = []
        self._undo.clear()
        self._selection = None
        if self.img is not None:
            self.view.show_array(self.img)

    def _on_selection(self, x: int, y: int, w: int, h: int) -> None:
        self._selection = (x, y, w, h)
        self.status.setText(f"Selection: {w}×{h} px at ({x}, {y})")

    def _push(self, op_name: str, new_img: np.ndarray, **params) -> None:
        self._undo.append(self.img)
        del self._undo[:-UNDO_DEPTH]
        self.img = new_img
        self.ops.append({"op": op_name, **params})
        self.view.show_array(self.img)

    # -- operations ---------------------------------------------------------------
    def _apply(self, op: str, **params) -> None:
        if self.img is None:
            return
        if op == "rotate90":
            self._push(op, rotate90(self.img, params["turns"]), **params)
        elif op == "rotate_fine":
            self._push(op, rotate_fine(self.img, params["angle"]), **params)

    def _auto_deskew(self) -> None:
        if self.img is None:
            return
        fixed, angle = deskew(self.img)
        self._push("auto_deskew", fixed, angle=round(angle, 2))

    def _crop_selection(self) -> None:
        if self.img is None or self._selection is None:
            self.status.setText("Select a region first, then crop.")
            return
        x, y, w, h = self._selection
        self._push("crop", crop_rect(self.img, x, y, w, h),
                   x=x, y=y, w=w, h=h)
        self._selection = None

    def _auto_crop(self) -> None:
        if self.img is None:
            return
        self._push("auto_crop", auto_crop(self.img))

    def _whiteout_selection(self) -> None:
        if self.img is None or self._selection is None:
            self.status.setText("Select the black space first, then "
                                "white-out.")
            return
        x, y, w, h = self._selection
        self._push("whiteout", whiteout_rect(self.img, x, y, w, h),
                   x=x, y=y, w=w, h=h)
        self._selection = None

    def _auto_black_borders(self) -> None:
        if self.img is None:
            return
        fixed, margins = remove_black_borders(self.img)
        self._push("auto_black_borders", fixed, **margins)

    def _apply_enhance(self) -> None:
        if self.img is None:
            return
        b = self.brightness.value()
        c = self.contrast.value() / 100.0
        self._push("brightness_contrast",
                   adjust_brightness_contrast(self.img, b, c),
                   brightness=b, contrast=c)

    def _auto_enhance(self) -> None:
        if self.img is None:
            return
        profile = {"whiten_bg": True, "clahe": True, "despeckle": True}
        self._push("auto_enhance", apply_profile(self.img, profile))

    def _undo_last(self) -> None:
        if self._undo:
            self.img = self._undo.pop()
            if self.ops:
                self.ops.pop()
            self.view.show_array(self.img)

    def _reset(self) -> None:
        info = self.pages[self.current_page_no()]
        original = cv2.imread(info.get("original_path")
                              or info["working_path"], cv2.IMREAD_UNCHANGED)
        if original is not None:
            self._push("reset_to_original", original)

    # -- persistence ----------------------------------------------------------------
    def _save(self) -> None:
        if self.img is None:
            self.reject()
            return
        info = self.pages[self.current_page_no()]
        cv2.imwrite(info["working_path"], self.img)
        if self.audit and self.ops:
            self.audit.log("manual_edit", self.username,
                           document_id=self.document_id,
                           page=self.current_page_no(), operations=self.ops)
        self.saved.emit(self.document_id, int(self.current_page_no()))
        self.accept()
