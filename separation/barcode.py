"""Barcode decoding for document separation (HLD §4 Layer 4).

pyzbar needs the native libzbar DLL; on machines without it we log a
warning once and return no barcodes so the blank-page fallback still
separates documents (HLD §20 risk mitigation).
"""
from __future__ import annotations

import logging

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


def decode_barcodes(img: np.ndarray) -> list[dict]:
    """Decode all barcodes on a page.

    Returns [{'type': 'QRCODE', 'value': 'BATCH-42', 'rect': (x, y, w, h)}].
    Empty list when no barcode found or decoder unavailable.
    """
    mod = _load_pyzbar()
    if mod is None:
        return []
    results = []
    for sym in mod.decode(img):
        results.append({
            "type": sym.type,
            "value": sym.data.decode("utf-8", errors="replace"),
            "rect": (sym.rect.left, sym.rect.top, sym.rect.width, sym.rect.height),
        })
    return results
