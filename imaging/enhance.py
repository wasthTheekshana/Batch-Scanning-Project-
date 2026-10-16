"""Enhancement pipeline: background whitening, CLAHE, despeckle
(HLD §4 Layer 3). Driven entirely by the project's enhancement_profile
toggles; disabled steps leave pixels untouched.
"""
from __future__ import annotations

import cv2
import numpy as np


def whiten_background(img: np.ndarray) -> np.ndarray:
    """Remove shadows/yellowing: model the background with dilate+median
    blur, then normalise by division."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    kernel = np.ones((7, 7), np.uint8)
    background = cv2.medianBlur(cv2.dilate(gray, kernel), 21)
    normalised = cv2.divide(gray, background, scale=255)
    if img.ndim == 3:
        # apply the same gain per channel to keep colour data
        gain = cv2.divide(normalised.astype(np.float32),
                          np.maximum(gray.astype(np.float32), 1))
        out = np.clip(img.astype(np.float32) * gain[..., None], 0, 255)
        return out.astype(np.uint8)
    return normalised


# CLAHE exists for faded pages. On a page that already has strong contrast it only
# greys the ink and amplifies scanner noise (measured on real scans), so it is applied
# only when the page is actually flat.
CLAHE_BELOW = 0.6


def contrast(img: np.ndarray) -> float:
    """Michelson contrast of the page between its 2nd and 98th percentile."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    lo, hi = (float(v) for v in np.percentile(gray, (2, 98)))
    return 0.0 if hi + lo == 0 else (hi - lo) / (hi + lo)


def apply_clahe(img: np.ndarray, clip_limit: float = 2.0) -> np.ndarray:
    """Local contrast enhancement on the L channel in LAB space."""
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(8, 8))
    if img.ndim == 3:
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        lab[..., 0] = clahe.apply(lab[..., 0])
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    return clahe.apply(img)


def despeckle(img: np.ndarray, dpi: int = 300) -> np.ndarray:
    """Remove dust: isolated dark specks only, painted out with the paper colour.

    The earlier median-blur + opening smoothed every stroke (text lost about
    40% of its sharpness and went grey). A speck is a tiny blob of ink with no
    neighbours; letters, the dot of an i, full stops and thin lines are larger
    than that and stay exactly as scanned."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    _t, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    count, labels, stats, _c = cv2.connectedComponentsWithStats(ink, connectivity=8)
    limit = max(1, int(round(SPECK_AREA_300DPI * (dpi / 300.0) ** 2)))
    small = np.flatnonzero(stats[1:, cv2.CC_STAT_AREA] <= limit) + 1
    if small.size == 0:
        return img
    dust = np.isin(labels, small)
    out = img.copy()
    paper = np.percentile(gray, 95)
    if img.ndim == 3:
        out[dust] = np.percentile(img.reshape(-1, 3), 95, axis=0).astype(img.dtype)
    else:
        out[dust] = int(paper)
    return out


SPECK_AREA_300DPI = 3     # pixels at 300 dpi (a full stop is about 8 to 12)


def binarize(img: np.ndarray, dpi: int = 300, k: float = 0.2) -> np.ndarray:
    """Black and white from a grayscale scan, locally adaptive (Sauvola): thin
    strokes and faint print survive and shadows do not turn into black. A scanner's
    own 1-bit mode uses one fixed threshold for the whole page, which loses both."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    g = gray.astype(np.float32)
    win = max(15, int(round(25 * dpi / 300.0))) | 1
    mean = cv2.boxFilter(g, -1, (win, win), borderType=cv2.BORDER_REPLICATE)
    sq = cv2.boxFilter(g * g, -1, (win, win), borderType=cv2.BORDER_REPLICATE)
    std = np.sqrt(np.maximum(sq - mean * mean, 0.0))
    threshold = mean * (1.0 + k * (std / 128.0 - 1.0))
    # Sauvola reads a solid dark area (a stamp, a logo, a dark photo) as "no
    # variation, so paper" and would hollow it out: anything far darker than the
    # page's paper level is ink wherever it is.
    paper = float(np.percentile(g, 90))
    black = (g <= threshold) | (g < 0.45 * paper)
    return np.where(black, 0, 255).astype(np.uint8)


def to_color_mode(img: np.ndarray, mode: str, dpi: int = 300) -> np.ndarray:
    """Enforce project colour mode after enhancement (bw/grayscale/color)."""
    if mode == "color":
        return img if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    if mode == "grayscale":
        return gray
    if mode == "bw":
        return binarize(gray, dpi)
    raise ValueError(f"unknown color mode: {mode}")


def apply_profile(img: np.ndarray, profile: dict, dpi: int = 300) -> np.ndarray:
    """Run enabled enhancement steps in fixed order:
    whiten background -> CLAHE (flat pages only) -> remove dust. Deskew/crop are
    separate modules invoked by the pipeline before enhancement."""
    out = img
    if profile.get("whiten_bg"):
        out = whiten_background(out)
    if profile.get("clahe") and contrast(out) < CLAHE_BELOW:
        out = apply_clahe(out)
    if profile.get("despeckle"):
        out = despeckle(out, dpi)
    return out
