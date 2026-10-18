"""Shared synthetic-image helpers for the test suite."""
from __future__ import annotations

import pytest

try:                                    # the image libraries: absent on a server-only machine, where
    import cv2                          # only tests/central runs (CI's central job); the helpers
    import numpy as np                  # below are then never called
except ImportError:                     # pragma: no cover
    cv2 = np = None


def make_text_page(w=800, h=1000, lines=12, color=False) -> np.ndarray:
    """White page with horizontal black text-like bars."""
    page = np.full((h, w, 3) if color else (h, w), 255, np.uint8)
    for i in range(lines):
        y = 100 + i * 60
        cv2.rectangle(page, (100, y), (w - 100, y + 18), 0, -1)
    cv2.putText(page, "INVOICE INV-1234", (100, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, 0, 3)
    return page


def make_blank_page(w=800, h=1000) -> np.ndarray:
    return np.full((h, w), 255, np.uint8)


def rotate(img: np.ndarray, angle: float) -> np.ndarray:
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    white = (255, 255, 255) if img.ndim == 3 else 255
    return cv2.warpAffine(img, m, (w, h), borderMode=cv2.BORDER_CONSTANT,
                          borderValue=white)


@pytest.fixture()
def text_page():
    return make_text_page()


@pytest.fixture()
def blank_page():
    return make_blank_page()
