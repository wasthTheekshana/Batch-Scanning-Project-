"""Manual page-correction operations for the QC page editor.

Every function is pure (image in → image out) so the editor can stack
operations, preview them, and log exactly what the operator applied.
The original scan is never touched — edits apply to the working copy.
"""
from __future__ import annotations

import cv2
import numpy as np

BLACK_THRESHOLD = 60          # pixel value considered "black space"
BORDER_DARK_FRACTION = 0.6    # row/col must be this dark to count as border


def rotate90(img: np.ndarray, turns: int) -> np.ndarray:
    """Rotate by 90° steps: turns=1 → 90° CW, -1 → 90° CCW, 2 → 180°."""
    mapping = {1: cv2.ROTATE_90_CLOCKWISE,
               -1: cv2.ROTATE_90_COUNTERCLOCKWISE,
               2: cv2.ROTATE_180, -2: cv2.ROTATE_180}
    turns = ((turns + 1) % 4) - 1 if turns % 4 != 2 else 2
    if turns == 0:
        return img
    return cv2.rotate(img, mapping[turns])


def rotate_fine(img: np.ndarray, angle_deg: float) -> np.ndarray:
    """Free rotation (degrees, positive = counter-clockwise); white fill."""
    if abs(angle_deg) < 0.01:
        return img
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle_deg, 1.0)
    white = (255, 255, 255) if img.ndim == 3 else 255
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=white)


def crop_rect(img: np.ndarray, x: int, y: int, w: int, h: int) -> np.ndarray:
    """Crop to the selected rectangle (image pixel coords, clamped)."""
    ih, iw = img.shape[:2]
    x0 = max(0, min(int(x), iw - 1))
    y0 = max(0, min(int(y), ih - 1))
    x1 = max(x0 + 1, min(int(x + w), iw))
    y1 = max(y0 + 1, min(int(y + h), ih))
    return img[y0:y1, x0:x1].copy()


def whiteout_rect(img: np.ndarray, x: int, y: int, w: int, h: int,
                  color: int = 255) -> np.ndarray:
    """Fill the selected rectangle with white (remove black space,
    punch shadows, staple marks, etc.)."""
    out = img.copy()
    ih, iw = out.shape[:2]
    x0 = max(0, min(int(x), iw))
    y0 = max(0, min(int(y), ih))
    x1 = max(x0, min(int(x + w), iw))
    y1 = max(y0, min(int(y + h), ih))
    fill = (color, color, color) if out.ndim == 3 else color
    out[y0:y1, x0:x1] = fill
    return out


def adjust_brightness_contrast(img: np.ndarray, brightness: int = 0,
                               contrast: float = 1.0) -> np.ndarray:
    """brightness −100..100 (additive), contrast 0.5..2.0 (gain)."""
    return cv2.convertScaleAbs(img, alpha=float(contrast),
                               beta=float(brightness))


def remove_black_borders(img: np.ndarray) -> tuple[np.ndarray, dict]:
    """Automatically white-fill dark scanner borders (black space left by
    the platen or a smaller original). Returns (image, margins_filled)."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    h, w = gray.shape
    dark = gray < BLACK_THRESHOLD

    def scan(axis_values) -> int:
        count = 0
        for frac in axis_values:
            if frac >= BORDER_DARK_FRACTION:
                count += 1
            else:
                break
        return count

    top = scan(dark.mean(axis=1))
    bottom = scan(dark.mean(axis=1)[::-1])
    left = scan(dark.mean(axis=0))
    right = scan(dark.mean(axis=0)[::-1])

    out = img.copy()
    white = (255, 255, 255) if out.ndim == 3 else 255
    if top:
        out[:top, :] = white
    if bottom:
        out[h - bottom:, :] = white
    if left:
        out[:, :left] = white
    if right:
        out[:, w - right:] = white
    margins = {"top": top, "bottom": bottom, "left": left, "right": right}
    return out, margins
