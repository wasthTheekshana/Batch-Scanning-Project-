"""Barcode decoding for document separation (HLD §4 Layer 4).

pyzbar needs the native libzbar DLL; on machines without it we log a
warning once and return no barcodes so the blank-page fallback still
separates documents (HLD §20 risk mitigation).
"""
from __future__ import annotations

import logging
import re

import cv2
import numpy as np

log = logging.getLogger(__name__)

_pyzbar = None
_pyzbar_failed = False


def _load_pyzbar():
    global _pyzbar, _pyzbar_failed
    if _pyzbar is None and not _pyzbar_failed:
        try:
            from pyzbar import pyzbar as mod
            _pyzbar = mod
        except Exception as e:  # ImportError or missing DLL (OSError)
            _pyzbar_failed = True
            log.warning("pyzbar unavailable (%s); barcode separation disabled", e)
    return _pyzbar


_ROTATIONS = (None, cv2.ROTATE_180, cv2.ROTATE_90_CLOCKWISE,
              cv2.ROTATE_90_COUNTERCLOCKWISE)


def decode_barcodes(img: np.ndarray) -> list[dict]:
    """Decode all barcodes on a page, whichever way up the page was fed.

    Returns [{'type': 'QRCODE', 'value': 'BATCH-42', 'rect': (x, y, w, h)}].
    Empty list when no barcode found or decoder unavailable. The page is
    tried upright first, then turned 180, 90 and 270 degrees (rect is in
    the coordinates of the orientation that decoded).
    """
    mod = _load_pyzbar()
    if mod is None:
        return []
    for rotation in _ROTATIONS:
        view = img if rotation is None else cv2.rotate(img, rotation)
        results = [{
            "type": sym.type,
            "value": sym.data.decode("utf-8", errors="replace"),
            "rect": (sym.rect.left, sym.rect.top, sym.rect.width,
                     sym.rect.height),
        } for sym in mod.decode(view)]
        if results:
            return results
    return []


def _symbology(name: str) -> str:
    key = re.sub(r"[^A-Z0-9]", "", str(name).upper())
    return {"QR": "QRCODE"}.get(key, key)


def filter_barcodes(found: list[dict], separation_cfg: dict) -> list[dict]:
    """Keep the barcodes this project separates by: only the listed
    symbologies (separation.barcode_types) and only values matching
    separation.barcode_pattern. Documents often carry other barcodes
    (product codes, cheque numbers) that must not split them. A pattern
    that is not a valid regex matches nothing."""
    types = {_symbology(t) for t in separation_cfg.get("barcode_types") or []}
    pattern = separation_cfg.get("barcode_pattern")
    kept = []
    for code in found:
        if types and _symbology(code["type"]) not in types:
            continue
        if pattern:
            try:
                if not re.search(pattern, code["value"]):
                    continue
            except re.error:
                continue
        kept.append(code)
    return kept
