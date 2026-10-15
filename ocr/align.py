"""Lining a scanned form up with its template.

Zones for a bank form are drawn once, on the blank form. A scan of a printed
copy is never at the same place or size: printers shrink to fit and add
margins, the paper goes in slightly crooked. Reading regions at their
template positions then lands in the wrong boxes.

The form's own printed words are the fix. Each document type can list
`anchors`: distinctive words of the blank form with the place (centre, in the
template's own units) where each sits. After OCR, the same words are found in
the scan, and a similarity transform (shift, scale, small rotation) mapping
the template to the scan is fitted to them. Words that occur twice (both
columns of a two-applicant form) are simply outliers for the fit.
"""
from __future__ import annotations

import re

import numpy as np

MIN_ANCHORS = 5            # matched words needed for a trustworthy fit
MIN_WORD_LENGTH = 3


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def build_anchors(words: list[dict], limit: int = 120) -> list[dict]:
    """Anchors from the words of a BLANK form: distinctive words, i.e. those
    that occur once or twice (a two-applicant form prints every label in both
    columns; the fit tells the right pair from the wrong one). Every
    occurrence is kept. words: [{"text", "x", "y"}], x and y the word's centre."""
    counts: dict[str, int] = {}
    for word in words:
        counts[_key(word["text"])] = counts.get(_key(word["text"]), 0) + 1
    anchors = []
    for word in words:
        key = _key(word["text"])
        if len(key) >= 4 and key.isalpha() and counts[key] <= 2:
            anchors.append({"t": key, "x": round(word["x"], 1),
                            "y": round(word["y"], 1)})
    return anchors[:limit]


def estimate_transform(anchors: list[dict], words: list[dict],
                       tolerance: float = 15.0) -> np.ndarray | None:
    """The 2x3 matrix taking template coordinates to scan pixels, or None
    when too few of the anchor words are found to fit it reliably.
    words: OCR words of the scan [{"text", "left", "top", "width", "height"}]."""
    import cv2
    found: dict[str, list[tuple[float, float]]] = {}
    for word in words:
        key = _key(word.get("text", ""))
        if len(key) >= MIN_WORD_LENGTH:
            found.setdefault(key, []).append(
                (word["left"] + word["width"] / 2, word["top"] + word["height"] / 2))
    src, dst, keys = [], [], []
    for anchor in anchors:
        for centre in found.get(anchor["t"], ()):
            src.append((anchor["x"], anchor["y"]))
            dst.append(centre)
            keys.append(anchor["t"])
    if len(set(keys)) < MIN_ANCHORS:
        return None
    matrix, inliers = cv2.estimateAffinePartial2D(
        np.float32(src), np.float32(dst), method=cv2.RANSAC,
        ransacReprojThreshold=tolerance, maxIters=3000, confidence=0.995)
    if matrix is None or inliers is None:
        return None
    good = {keys[i] for i, flag in enumerate(inliers.ravel()) if flag}
    scale = float(np.hypot(matrix[0, 0], matrix[1, 0]))
    if len(good) < MIN_ANCHORS or not 0.3 < scale < 20:
        return None
    return matrix


def dewarp(img: np.ndarray, matrix: np.ndarray, ref_w: float,
           ref_h: float) -> np.ndarray:
    """The scan straightened onto the template: shift, scale and rotation
    undone, so the template's rectangles hold again. The result is
    ref_w x ref_h template units at the scan's own pixel density (the zone
    reader then scales a zone by the image width over ref_w, as it always
    does), so no resolution is lost and boxes that were slanted by a crooked
    page are straight again."""
    import cv2
    forward = np.vstack([matrix, [0.0, 0.0, 1.0]])
    density = float(np.hypot(matrix[0, 0], matrix[1, 0]))     # pixels per unit
    to_template = np.diag([density, density, 1.0]) @ np.linalg.inv(forward)
    size = (int(round(ref_w * density)), int(round(ref_h * density)))
    return cv2.warpAffine(img, to_template[:2], size, flags=cv2.INTER_LINEAR,
                          borderValue=255)


STRAIGHT_ENOUGH = 0.25      # degrees: a page turned less than this is not resampled


def rotation_degrees(matrix: np.ndarray) -> float:
    return float(np.degrees(np.arctan2(matrix[1, 0], matrix[0, 0])))


def map_zone(zone: dict, matrix: np.ndarray) -> dict:
    """The zone's region in scan pixels: the template rectangle's corners
    carried through the transform. The zone loses its ref size, because it is
    now in absolute pixels. Used for pages that are straight enough to read in
    place: resampling a whole page blurs the thin box lines of a form, so it
    is done only when the page is actually turned."""
    x, y, w, h = (float(zone[k]) for k in ("x", "y", "w", "h"))
    corners = np.array([[x, y, 1], [x + w, y, 1], [x, y + h, 1], [x + w, y + h, 1]])
    mapped = corners @ matrix.T
    x0, y0 = mapped.min(axis=0)
    x1, y1 = mapped.max(axis=0)
    out = {k: v for k, v in zone.items() if k not in ("ref_w", "ref_h", "anchors")}
    out.update(x=int(round(x0)), y=int(round(y0)),
               w=int(round(x1 - x0)), h=int(round(y1 - y0)))
    return out
