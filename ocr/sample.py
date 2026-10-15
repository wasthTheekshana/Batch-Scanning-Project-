"""Setting up a form from a blank copy: render it and learn its anchor words.

The anchors are the form's own printed words with their places, in the pixels
of the rendered sample. Zones drawn on the same sample (ZoneDialog renders it
the same way) live in that same frame, which is what lets the reader line them
up with any later scan of the form.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ocr.align import MIN_ANCHORS, build_anchors

SAMPLE_DPI = 300
GOOD_ANCHORS = 12          # fewer than this and a crooked scan may not line up


def load_sample_image(path: str | Path, page: int = 0) -> np.ndarray | None:
    """The blank form as an image: a PDF page at 300 DPI (as the import
    pipeline renders it) or an image file. None when it cannot be read."""
    path = str(path)
    if path.lower().endswith(".pdf"):
        import fitz
        with fitz.open(path) as doc:
            if not 0 <= page < doc.page_count:
                return None
            pix = doc[page].get_pixmap(dpi=SAMPLE_DPI)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n)
            return (cv2.cvtColor(img[..., :3], cv2.COLOR_RGB2BGR)
                    if pix.n >= 3 else img[..., 0].copy())
    return cv2.imread(path, cv2.IMREAD_UNCHANGED)


def learn_anchors(img: np.ndarray, ocr_fn=None) -> list[dict]:
    """Anchor words of a blank form image. ocr_fn(img) -> object with .words
    (injectable for tests); default is Tesseract."""
    if ocr_fn is None:
        from ocr.tesseract_engine import ocr_page
        ocr_fn = ocr_page
    words = [{"text": w["text"], "x": w["left"] + w["width"] / 2,
              "y": w["top"] + w["height"] / 2}
             for w in (ocr_fn(img).words or [])]
    return build_anchors(words)


def describe(anchors: list[dict]) -> str:
    """One plain sentence for the Designer about how usable the anchors are."""
    n = len({a["t"] for a in anchors})
    if n < MIN_ANCHORS:
        return (f"Only {n} distinctive printed word(s) found: too few to line "
                "up scans. Use a sharper, fuller blank form.")
    if n < GOOD_ANCHORS:
        return (f"{n} anchor words learned. This works for straight scans; a "
                "form with more printed text lines up more reliably.")
    return f"{n} anchor words learned: scans of this form will line up."
