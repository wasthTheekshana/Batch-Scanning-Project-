"""A small handwritten-digit recogniser for boxed number fields.

Tesseract reads typed digits perfectly but handwritten ones (neat capitals in
a form's boxes) about half the time. This is a tiny two-layer network over the
8x8 digit format of the classic "optdigits" set, trained by
scripts/train_digits.py; its weights (a few KB, ocr/digit_model.npz) run here
with plain numpy, so nothing extra is shipped with the application.

A glyph is turned into the same 8x8 form the network was trained on: cut to
its bounding box, fitted into a 32x32 bitmap keeping its proportions, and
summed in 4x4 blocks (0..16 per block).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np

MODEL_PATH = Path(__file__).with_name("digit_model.npz")
MIN_INK = 0.012          # a cell with less ink than this has no digit in it


STROKE_TARGET = 3.0      # pen width (pixels, at 32x32) the network was trained on


def _thin(canvas: np.ndarray, target: float | None = None) -> np.ndarray:
    """Pen strokes in a scan are far heavier than the training digits'; fill
    the shapes in and every digit looks alike. Estimate the stroke width
    (twice the area over the perimeter) and erode down to the training width."""
    import cv2
    binary = (canvas > 90).astype(np.uint8)
    contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    perimeter = sum(cv2.arcLength(c, True) for c in contours)
    if perimeter <= 0:
        return canvas
    width = 2.0 * float(binary.sum()) / perimeter
    steps = int(round((width - (STROKE_TARGET if target is None else target)) / 2.0))
    thinned = binary
    cross = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    for _ in range(max(0, steps)):
        eroded = cv2.erode(thinned, cross)
        if eroded.sum() < 0.25 * binary.sum():        # never erase the glyph
            break
        thinned = eroded
    return thinned * 255


def _canvas(ink: np.ndarray) -> np.ndarray | None:
    """The glyph cut to its bounding box and fitted, in proportion, into 32x32."""
    import cv2
    binary = (ink > 0).astype(np.uint8)
    if binary.size == 0 or binary.mean() < MIN_INK:
        return None
    ys, xs = np.nonzero(binary)
    glyph = binary[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    h, w = glyph.shape
    scale = 28.0 / max(h, w)
    glyph = cv2.resize(glyph * 255, (max(1, round(w * scale)), max(1, round(h * scale))),
                       interpolation=cv2.INTER_AREA)
    canvas = np.zeros((32, 32), np.uint8)
    top, left = (32 - glyph.shape[0]) // 2, (32 - glyph.shape[1]) // 2
    canvas[top:top + glyph.shape[0], left:left + glyph.shape[1]] = glyph
    return canvas


def _features(canvas: np.ndarray, target: float | None) -> np.ndarray:
    bits = (_thin(canvas, target) > 90).astype(np.float32)
    blocks = bits.reshape(8, 4, 8, 4).sum(axis=(1, 3))          # 0..16
    return (blocks / 16.0).reshape(64)


def glyph_to_features(ink: np.ndarray) -> np.ndarray | None:
    """64 features (0..1) from a cell image (non-zero = ink), or None when
    the cell holds no glyph."""
    canvas = _canvas(ink)
    return None if canvas is None else _features(canvas, None)


# a pen's weight is not exactly measurable on a scan: read the glyph at a few
# nearby weights and average the network's answers
WEIGHTS = (2.5, 3.0, 3.5)


@lru_cache(maxsize=1)
def _model():
    if not MODEL_PATH.exists():
        return None
    data = np.load(MODEL_PATH)
    return {k: data[k] for k in ("w1", "b1", "w2", "b2")}


def available() -> bool:
    return _model() is not None


def _probabilities(features: np.ndarray) -> np.ndarray:
    m = _model()
    hidden = np.maximum(0.0, features @ m["w1"] + m["b1"])
    logits = hidden @ m["w2"] + m["b2"]
    logits -= logits.max()
    probs = np.exp(logits)
    return probs / probs.sum()


def predict(features: np.ndarray) -> tuple[str, float]:
    """(digit, probability) for one feature vector."""
    probs = _probabilities(features)
    best = int(probs.argmax())
    return str(best), float(probs[best])


def read_digit(ink: np.ndarray) -> tuple[str, float]:
    """(digit, probability) from a cell image, ("", 0) when it is empty or
    the model is not installed. The probabilities from several pen-weight
    readings are averaged, which steadies borderline handwriting."""
    if not available():
        return "", 0.0
    canvas = _canvas(ink)
    if canvas is None:
        return "", 0.0
    total = np.zeros(10)
    for weight in WEIGHTS:
        total += _probabilities(_features(canvas, weight))
    total /= len(WEIGHTS)
    best = int(total.argmax())
    return str(best), float(total[best])
