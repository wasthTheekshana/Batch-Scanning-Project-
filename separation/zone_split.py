"""Zone-based document boundary trigger (HLD §4 Layer 4).

OCR a named rectangular region of the page; when the text matches the
zone's regex the page starts a new document.
"""
from __future__ import annotations

import re
from typing import Callable

import numpy as np


def check_zone_trigger(img: np.ndarray, zone: dict,
                       ocr_fn: Callable[[np.ndarray], str]) -> bool:
    """zone: {'x': int, 'y': int, 'w': int, 'h': int, 'regex': str}.
    Coordinates are pixels in the page image. Out-of-range zones are
    clamped; an empty crop never triggers."""
    h, w = img.shape[:2]
    x0 = max(0, min(int(zone["x"]), w - 1))
    y0 = max(0, min(int(zone["y"]), h - 1))
    x1 = max(x0 + 1, min(x0 + int(zone["w"]), w))
    y1 = max(y0 + 1, min(y0 + int(zone["h"]), h))
    region = img[y0:y1, x0:x1]
    text = ocr_fn(region) or ""
    return re.search(zone["regex"], text) is not None
