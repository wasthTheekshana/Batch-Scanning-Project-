"""Auto-crop to content bounding box with safe margin (HLD §4 Layer 3)."""
from __future__ import annotations

import cv2
import numpy as np

DEFAULT_MARGIN_PX = 40  # at 300 DPI per HLD


def content_bbox(img: np.ndarray) -> tuple[int, int, int, int] | None:
    """(x, y, w, h) of the outermost content boundary, or None if blank."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    _, binary = cv2.threshold(gray, 0, 255,
                              cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    xs, ys, xe, ye = [], [], [], []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        xs.append(x); ys.append(y); xe.append(x + w); ye.append(y + h)
    x0, y0, x1, y1 = min(xs), min(ys), max(xe), max(ye)
    return x0, y0, x1 - x0, y1 - y0


def auto_crop(img: np.ndarray, margin_px: int = DEFAULT_MARGIN_PX) -> np.ndarray:
    """Crop to content plus safe margin. Never crops inside the content
    bbox; returns the original image when no content is found."""
    bbox = content_bbox(img)
    if bbox is None:
        return img
    x, y, w, h = bbox
    ih, iw = img.shape[:2]
    x0 = max(0, x - margin_px)
    y0 = max(0, y - margin_px)
    x1 = min(iw, x + w + margin_px)
    y1 = min(ih, y + h + margin_px)
    return img[y0:y1, x0:x1]
