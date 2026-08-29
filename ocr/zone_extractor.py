"""Named-zone field extraction (HLD §4 Layer 5).

Zones are rectangles configured per project; each yields one metadata
field. Extracted text is cleaned with the zone's regex (first group or
whole match) and returned with the OCR confidence.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable

import numpy as np

from ocr.tesseract_engine import OCRResult, ocr_page

log = logging.getLogger(__name__)


@dataclass
class FieldValue:
    value: str
    confidence: float


def _extract_match_value(pattern: str, text: str) -> str | None:
    """Extract value from regex match: first capture group if present,
    otherwise the entire match. Returns None if no match, or the extracted
    string (possibly empty for matched-but-empty captures)."""
    m = re.search(pattern, text)
    return (m.group(1) if m.lastindex else m.group(0)) if m else None


def scaled_rect(zone: dict, img_w: int, img_h: int) -> tuple[int, int, int, int]:
    """Zone rect in this image's pixels. Zones drawn on a sample of a
    different resolution carry ref_w/ref_h and are scaled proportionally
    so extraction is resolution-independent."""
    x, y, w, h = int(zone["x"]), int(zone["y"]), int(zone["w"]), int(zone["h"])
    ref_w, ref_h = zone.get("ref_w"), zone.get("ref_h")
    if ref_w and ref_h and (int(ref_w) != img_w or int(ref_h) != img_h):
        sx, sy = img_w / int(ref_w), img_h / int(ref_h)
        x, y, w, h = round(x * sx), round(y * sy), round(w * sx), round(h * sy)
    return x, y, w, h


def extract_fields(img: np.ndarray, zones: list[dict], lang: str = "eng",
                   ocr_fn: Callable[[np.ndarray], OCRResult] | None = None
                   ) -> dict[str, FieldValue]:
    """zones: [{'name': 'invoice_no', 'x':..,'y':..,'w':..,'h':..,
    'regex': optional, 'ref_w'/'ref_h': optional sample dimensions}].
    ocr_fn is injectable for tests."""
    run_ocr = ocr_fn or (lambda region: ocr_page(region, lang=lang))
    h, w = img.shape[:2]
    fields: dict[str, FieldValue] = {}
    for zone in zones:
        if not all(k in zone for k in ("x", "y", "w", "h")):
            continue  # not yet positioned (e.g. a fresh template zone)
        zx, zy, zw, zh = scaled_rect(zone, w, h)
        x0 = max(0, min(zx, w - 1))
        y0 = max(0, min(zy, h - 1))
        x1 = max(x0 + 1, min(x0 + zw, w))
        y1 = max(y0 + 1, min(y0 + zh, h))
        result = run_ocr(img[y0:y1, x0:x1])
        value = result.text.strip()
        if zone.get("regex"):
            extracted = _extract_match_value(zone["regex"], value)
            value = extracted if extracted is not None else ""
        fields[zone["name"]] = FieldValue(value=value,
                                          confidence=result.confidence)
    return fields


def extract_fields_from_text(text: str, confidence: float,
                             zones: list[dict]) -> dict[str, FieldValue]:
    """Full-page mode: locate each field's value in already-OCR'd page
    text via its label-anchored regex, instead of cropping a fixed zone.
    A zone with no regex is skipped — unanchored, page-wide matching has
    no way to know which part of the page belongs to it."""
    fields: dict[str, FieldValue] = {}
    for zone in zones:
        pattern = zone.get("regex")
        if not pattern:
            log.warning("zone %r has no regex; skipped in full-page mode", zone["name"])
            continue
        value = _extract_match_value(pattern, text)
        if value is not None:
            fields[zone["name"]] = FieldValue(value=value, confidence=confidence)
        else:
            fields[zone["name"]] = FieldValue(value="", confidence=0.0)
    return fields
