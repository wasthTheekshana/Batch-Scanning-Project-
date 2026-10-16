"""Pages fed upside down or sideways: find the way up by reading.

The scanner's feeder takes mixed stacks, and an ID card or a form is easily put
in the wrong way round. Text read from a page that is turned wrong is
gibberish, so when the first reading of a page finds almost no real words the
page is read again turned 180, 90 and 270 degrees, and the turn that yields
the most real words wins. The page, its text and its index fields then all use
the upright version.
"""
from __future__ import annotations

import cv2
import numpy as np

# clockwise turns to try, most likely first (a sheet fed upside down)
TURNS = {180: cv2.ROTATE_180, 90: cv2.ROTATE_90_CLOCKWISE,
         270: cv2.ROTATE_90_COUNTERCLOCKWISE}
GOOD_ENOUGH = 6        # real words that mean the page already reads fine ...
GOOD_SHARE = 0.15      # ... if they are also this share of everything read (an
                       # upside-down full form still yields a few real words
                       # among hundreds of gibberish ones)
MIN_WORD = 4           # letters in a "real word"
MIN_CONF = 60


def real_words(result) -> int:
    """How many words of the reading look like real words (letters only,
    four or more, read with some confidence): gibberish has few."""
    if result is None:
        return 0
    return sum(1 for w in getattr(result, "words", None) or ()
               if len(w["text"]) >= MIN_WORD and w["text"].isalpha()
               and w.get("conf", 100) >= MIN_CONF)


def turn(image: np.ndarray, degrees: int) -> np.ndarray:
    """The image turned `degrees` clockwise (0, 90, 180 or 270)."""
    return image if degrees == 0 else cv2.rotate(image, TURNS[degrees])


OSD_TRUST = 1.5        # Tesseract's own orientation test counts when it is this sure
NEAR_BEST = 0.85       # turns reading within this share of the best are "about as good"


def _tesseract_osd(image: np.ndarray):
    """Tesseract's orientation test ({"rotate": clockwise degrees that make the
    page upright, "orientation_conf"}), or None when it cannot judge."""
    try:
        from ocr.tesseract_engine import ocr_osd
        return ocr_osd(image)
    except Exception:
        return None


def upright(image: np.ndarray, first, read, osd=None) -> tuple[int, np.ndarray, object]:
    """(degrees turned, the upright image, its reading). `first` is the
    reading of `image` as it came; `read(image)` reads another version.

    Counting real words cannot tell a page that is upright from one turned 90
    degrees (the engine reads sideways text almost as well), so Tesseract's
    orientation test decides whenever it is confident, and the turn it names is
    kept when the page reads at least as well that way. Without a confident
    verdict the words decide, preferring a sheet fed upside down (by far the
    commonest) when two turns read about equally well."""
    if not getattr(first, "words", None):
        return 0, image, first            # nothing to judge the page by
    current = real_words(first)
    reads_fine = current >= GOOD_ENOUGH and current >= GOOD_SHARE * len(first.words)
    verdict = (osd or _tesseract_osd)(image)
    if verdict and verdict.get("orientation_conf", 0) >= OSD_TRUST:
        degrees = int(verdict.get("rotate", 0)) % 360
        if degrees == 0 and reads_fine:
            return 0, image, first        # sure it is upright, and it reads
        if degrees in TURNS:
            candidate = turn(image, degrees)
            result = read(candidate)
            if real_words(result) >= max(MIN_WORD, current):
                return degrees, candidate, result
    elif reads_fine:
        return 0, image, first
    scored = []
    for degrees in TURNS:                 # 180, 90, 270: most likely first
        candidate = turn(image, degrees)
        result = read(candidate)
        scored.append((real_words(result), degrees, candidate, result))
    top = max(score for score, *_ in scored)
    # only turn the page on clear evidence: a faint page that reads about as
    # badly every way up must be left as it is
    if top >= MIN_WORD and top >= 2 * current + 2:
        for score, degrees, candidate, result in scored:
            if score >= NEAR_BEST * top:
                return degrees, candidate, result
    return 0, image, first
