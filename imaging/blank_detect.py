"""Blank-page classifier: ink-pixel density below a configurable
threshold marks the page as a separator candidate (HLD §4 Layer 3)."""
from __future__ import annotations

import cv2
import numpy as np

DEFAULT_THRESHOLD = 0.02


def ink_density(img: np.ndarray) -> float:
    """Fraction of pixels considered 'ink' after Otsu binarisation,
    measured away from the outer 3% border to ignore scanner edges."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    h, w = gray.shape
    mh, mw = max(1, int(h * 0.03)), max(1, int(w * 0.03))
    inner = gray[mh:h - mh, mw:w - mw]
    blurred = cv2.GaussianBlur(inner, (3, 3), 0)
    _, binary = cv2.threshold(blurred, 200, 255, cv2.THRESH_BINARY_INV)
    return float(np.count_nonzero(binary)) / binary.size


def is_blank(img: np.ndarray, threshold: float = DEFAULT_THRESHOLD) -> bool:
    return ink_density(img) < threshold
