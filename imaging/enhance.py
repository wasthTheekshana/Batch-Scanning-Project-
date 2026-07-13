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


def apply_clahe(img: np.ndarray, clip_limit: float = 2.0) -> np.ndarray:
    """Local contrast enhancement on the L channel in LAB space."""
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(8, 8))
    if img.ndim == 3:
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        lab[..., 0] = clahe.apply(lab[..., 0])
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    return clahe.apply(img)


def despeckle(img: np.ndarray) -> np.ndarray:
    """Remove salt-and-pepper noise with median blur + morphological open."""
    out = cv2.medianBlur(img, 3)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))
    return cv2.morphologyEx(out, cv2.MORPH_OPEN, kernel)


def to_color_mode(img: np.ndarray, mode: str) -> np.ndarray:
    """Enforce project colour mode after enhancement (bw/grayscale/color)."""
    if mode == "color":
        return img if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    if mode == "grayscale":
        return gray
    if mode == "bw":
        _, bw = cv2.threshold(gray, 0, 255,
                              cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return bw
    raise ValueError(f"unknown color mode: {mode}")


def apply_profile(img: np.ndarray, profile: dict) -> np.ndarray:
    """Run enabled enhancement steps in fixed order:
    whiten background → CLAHE → despeckle. Deskew/crop are separate
    modules invoked by the pipeline before enhancement."""
    out = img
    if profile.get("whiten_bg"):
        out = whiten_background(out)
    if profile.get("clahe"):
        out = apply_clahe(out)
    if profile.get("despeckle"):
        out = despeckle(out)
    return out
