"""Named-zone field extraction (HLD §4 Layer 5).

Zones are rectangles configured per project; each yields one metadata
field. Extracted text is cleaned with the zone's regex (first group or
whole match) and returned with the OCR confidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

import numpy as np

from ocr.tesseract_engine import OCRResult, ocr_page


@dataclass
class FieldValue:
    value: str
    confidence: float


def extract_fields(img: np.ndarray, zones: list[dict], lang: str = "eng",
                   ocr_fn: Callable[[np.ndarray], OCRResult] | None = None
                   ) -> dict[str, FieldValue]:
    """zones: [{'name': 'invoice_no', 'x':..,'y':..,'w':..,'h':..,
    'regex': optional}]. ocr_fn is injectable for tests."""
    run_ocr = ocr_fn or (lambda region: ocr_page(region, lang=lang))
    h, w = img.shape[:2]
    fields: dict[str, FieldValue] = {}
    for zone in zones:
        x0 = max(0, min(int(zone["x"]), w - 1))
        y0 = max(0, min(int(zone["y"]), h - 1))
        x1 = max(x0 + 1, min(x0 + int(zone["w"]), w))
        y1 = max(y0 + 1, min(y0 + int(zone["h"]), h))
        result = run_ocr(img[y0:y1, x0:x1])
        value = result.text.strip()
        if zone.get("regex"):
            m = re.search(zone["regex"], value)
            value = (m.group(1) if m.lastindex else m.group(0)) if m else ""
        fields[zone["name"]] = FieldValue(value=value,
                                          confidence=result.confidence)
    return fields
