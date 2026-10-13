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
GOOD_ENOUGH = 6        # real words that mean the page already reads fine
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


def upright(image: np.ndarray, first, read) -> tuple[int, np.ndarray, object]:
    """(degrees turned, the upright image, its reading). `first` is the
    reading of `image` as it came; `read(image)` reads another version."""
    if not getattr(first, "words", None):
        return 0, image, first            # nothing to judge the page by
    current = real_words(first)
    if current >= GOOD_ENOUGH:
        return 0, image, first
    best_turn, best_image, best_result, best = 0, image, first, current
    for degrees in TURNS:
        candidate = turn(image, degrees)
        result = read(candidate)
        score = real_words(result)
        if score > best:
            best_turn, best_image, best_result, best = degrees, candidate, result, score
    # only turn the page on clear evidence: a faint page that reads about as
    # badly every way up must be left as it is
    if best_turn and best >= MIN_WORD and best >= 2 * current + 2:
        return best_turn, best_image, best_result
    return 0, image, first
