"""Reading a "comb" field: a value written one character per box, as on bank
forms (account number, NIC number, date of birth: [1][9][9][0]...). OCR on the
whole strip is thrown by the box borders, so the strip is cut into its cells
and each cell is read as a single character.

Forms print the boxes (and often pale hint letters such as D D M M Y Y Y Y) in
a colour lighter than the text that is typed or written into them, so only
marks darker than the box borders count as the entry.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np

from ocr.tesseract_engine import OCRResult

CHARSETS = {
    "digits": "0123456789",
    "letters": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
    "alnum": "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    "nic": "0123456789VvXx",
}
MIN_CELLS = 3          # fewer dividers than this: not a comb, read as a line
INK_MIN = 0.012        # a cell with less ink than this is empty
FIND_CUT = 150         # loose level used only to locate the box borders
INK_BELOW_BORDER = 15  # entry ink must be this much darker than the borders
INK_FLOOR, INK_CEIL = 40, 150
DEFAULT_INK = 140      # when no border level can be measured
WORKERS = 4            # cells read at the same time

# what an engine restricted to digits often returns instead of the digit
LOOKALIKES = {"g": "9", "q": "9", "O": "0", "o": "0", "D": "0", "Q": "0", "l": "1",
              "I": "1", "|": "1", "Z": "2", "z": "2", "S": "5", "s": "5",
              "B": "8", "b": "6", "T": "7"}


def _gray(strip: np.ndarray) -> np.ndarray:
    import cv2
    return cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY) if strip.ndim == 3 else strip


def _dark(gray: np.ndarray, level: int) -> np.ndarray:
    import cv2
    return cv2.threshold(gray, level, 255, cv2.THRESH_BINARY_INV)[1]


def _vertical_lines(binary: np.ndarray) -> np.ndarray:
    import cv2
    return cv2.morphologyEx(
        binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT,
                                  (1, max(5, int(binary.shape[0] * 0.8)))))


def find_cells(binary: np.ndarray) -> list[tuple[int, int]]:
    """[(x0, x1)] column spans between the vertical box dividers. Spans much
    narrower or wider than the typical cell (the "/" gaps in a date) are
    dropped."""
    height = binary.shape[0]
    on = (_vertical_lines(binary).sum(axis=0) / 255) > height * 0.75
    dividers, start = [], None
    for x, flag in enumerate(on):
        if flag and start is None:
            start = x
        elif not flag and start is not None:
            dividers.append((start + x - 1) // 2)
            start = None
    if start is not None:
        dividers.append((start + len(on) - 1) // 2)
    spans = list(zip(dividers, dividers[1:]))
    if len(spans) < 2:
        return []
    widths = sorted(b - a for a, b in spans)
    typical = widths[len(widths) // 2]
    return [(a, b) for a, b in spans if 0.6 * typical <= b - a <= 1.5 * typical]


def ink_level(gray: np.ndarray, loose: np.ndarray) -> int:
    """Grey level below which a pixel is entry ink: a little darker than the
    box borders (measured on the vertical divider pixels)."""
    lines = _vertical_lines(loose) > 0
    if np.count_nonzero(lines) < 10:
        return DEFAULT_INK
    border = int(np.median(gray[lines]))
    return max(INK_FLOOR, min(INK_CEIL, border - INK_BELOW_BORDER))


def _strip_lines(binary: np.ndarray) -> np.ndarray:
    import cv2
    h, w = binary.shape
    horizontal = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(5, int(w * 0.6)), 1)))
    lines = cv2.dilate(cv2.bitwise_or(horizontal, _vertical_lines(binary)),
                       np.ones((3, 3), np.uint8))
    return cv2.bitwise_and(binary, cv2.bitwise_not(lines))


def _ocr(image: np.ndarray, whitelist: str | None, psm: int) -> tuple[str, float]:
    from ocr.tesseract_engine import _pytesseract
    pt = _pytesseract()
    config = f"--psm {psm}"
    if whitelist:
        config += f" -c tessedit_char_whitelist={whitelist}"
    data = pt.image_to_data(image, output_type=pt.Output.DICT, config=config)
    best, conf = "", -1.0
    for text, c in zip(data["text"], data["conf"]):
        if text.strip() and float(c) >= 0 and float(c) > conf:
            best, conf = text.strip()[:1], float(c)
    return best, max(conf, 0.0) / 100.0


def _variants(cell: np.ndarray):
    """The cell enlarged in a few ways: one engine setting rarely reads every
    glyph right, so the readings are voted on."""
    import cv2
    for scale, grow in ((3, 2), (4, 0), (2, 0), (3, 0)):
        big = cv2.resize(cell, None, fx=scale, fy=scale,
                         interpolation=cv2.INTER_CUBIC)
        if grow:
            big = cv2.dilate(big, np.ones((grow, grow), np.uint8))
        yield cv2.copyMakeBorder(255 - big, 24, 24, 24, 24,
                                 cv2.BORDER_CONSTANT, value=255)


def _read_char(cell: np.ndarray, whitelist: str) -> tuple[str, float]:
    """One character from a cell image (white ink on black): the reading
    with the most total confidence across several enlargements. When none
    finds a character, an unrestricted reading is mapped back to a character
    the box allows (a digit box that reads "g" means 9)."""
    votes: dict[str, list[float]] = {}
    images = list(_variants(cell))
    for image in images:
        for psm in (10, 8):
            char, conf = _ocr(image, whitelist, psm)
            if char:
                votes.setdefault(char, []).append(conf)
                break
        if any(len(v) >= 2 for v in votes.values()):
            break                       # two readings agree: no need for more
    if not votes:                       # unrestricted readings, mapped back
        for image in images:
            for psm in (10, 8):
                char, conf = _ocr(image, None, psm)
                char = LOOKALIKES.get(char, char)
                if char and char in whitelist:
                    votes.setdefault(char, []).append(conf * 0.8)
                    break
    if not votes:
        return "", 0.0
    best = max(votes, key=lambda c: sum(votes[c]))
    return best, sum(votes[best]) / len(votes[best])


def read_comb(strip: np.ndarray, charset: str = "alnum") -> OCRResult:
    """The characters written in a strip of boxes, left to right."""
    whitelist = CHARSETS.get(charset, charset or CHARSETS["alnum"])
    gray = _gray(strip)
    loose = _dark(gray, FIND_CUT)
    marks = np.nonzero(loose)
    if len(marks[0]) == 0:
        return OCRResult(text="", confidence=0.0)
    # trim the slack the zone leaves around the boxes: all the size tests
    # below are relative to the boxes themselves
    top, bottom = marks[0].min(), marks[0].max() + 1
    left, right = marks[1].min(), marks[1].max() + 1
    gray, loose = gray[top:bottom, left:right], loose[top:bottom, left:right]
    cells = find_cells(loose)
    if len(cells) < MIN_CELLS:
        return OCRResult(text="", confidence=0.0)
    ink = _strip_lines(_dark(gray, ink_level(gray, loose)))
    height = ink.shape[0]
    margin = max(2, int(height * 0.06))
    inked = []
    for x0, x1 in cells:
        cell = ink[margin:height - margin, x0 + 5:x1 - 4]
        if cell.size and np.count_nonzero(cell) / cell.size >= INK_MIN:
            inked.append(cell)                         # empty boxes are skipped
    # each reading runs the OCR engine as a separate process: do several at once
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        readings = list(pool.map(lambda c: _read_char(c, whitelist), inked))
    chars = [char for char, _ in readings if char]
    confs = [conf for char, conf in readings if char]
    confidence = sum(confs) / len(confs) if confs else 0.0
    return OCRResult(text="".join(chars), confidence=confidence)
