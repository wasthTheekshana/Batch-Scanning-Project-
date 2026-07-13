"""Skew detection and correction (HLD §4 Layer 3).

HoughLinesP finds near-horizontal text lines; the median angle within
±15° is corrected with warpAffine on a white-filled canvas.
"""
from __future__ import annotations

import cv2
import numpy as np

MAX_CORRECTION_DEG = 15.0


def _to_gray(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img


def detect_skew_angle(img: np.ndarray) -> float:
    """Estimated skew in degrees (positive = counter-clockwise tilt).
    Returns 0.0 when no reliable line signal exists."""
    gray = _to_gray(img)
    edges = cv2.Canny(gray, 50, 150)
    min_len = max(gray.shape[1] // 4, 50)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 360, threshold=80,
                            minLineLength=min_len, maxLineGap=10)
    if lines is None:
        return 0.0
    angles = []
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
        angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        if abs(angle) <= MAX_CORRECTION_DEG:
            angles.append(angle)
    if not angles:
        return 0.0
    return float(np.median(angles))


def deskew(img: np.ndarray) -> tuple[np.ndarray, float]:
    """Rotate the image to correct detected skew. White border fill.
    Returns (corrected_image, applied_angle_degrees)."""
    angle = detect_skew_angle(img)
    if abs(angle) < 0.05:
        return img, 0.0
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    white = (255, 255, 255) if img.ndim == 3 else 255
    fixed = cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=white)
    return fixed, angle
