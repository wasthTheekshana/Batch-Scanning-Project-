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
FIND_CUTS = (FIND_CUT, 170, 190)    # pale or dotted box lines need a higher cut
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


def _vertical_lines(binary: np.ndarray, fraction: float = 0.8) -> np.ndarray:
    import cv2
    return cv2.morphologyEx(
        binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT,
                                  (1, max(5, int(binary.shape[0] * fraction)))))


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


DARK_BORDER = 80       # borders this dark: a black-and-white or black-ink form


def ink_level(gray: np.ndarray, loose: np.ndarray) -> int:
    """Grey level below which a pixel is entry ink. On a colour/grey form the
    printed boxes are lighter than what is typed in, so entry ink is a little
    darker than the borders (measured on the vertical divider pixels). On a
    black-and-white scan the borders are as dark as the ink: nothing can be
    told apart by darkness, so everything dark counts and the box lines are
    removed by shape instead."""
    lines = _vertical_lines(loose) > 0
    if np.count_nonzero(lines) < 10:
        return DEFAULT_INK
    border = int(np.median(gray[lines]))
    if border < DARK_BORDER:
        return DEFAULT_INK
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


CELL_HEIGHT = 40       # cells are brought to about this height before reading
# (size relative to CELL_HEIGHT, dilation) tried. Measured on all ten digits at
# 150-300 dpi: single-character mode at about the native size is exact, while
# the line modes and larger or thickened glyphs misread 1 as 4 and 6 as 5.
VARIANTS = ((1, 0), (0.8, 0), (1.25, 0))
PSMS = (10, 7)         # engine modes tried per variant (10: a single character)


def _variants(cell: np.ndarray):
    """The cell enlarged in a few ways: one engine setting rarely reads every
    glyph right, so the readings are voted on. The size is relative to the
    cell, so a 150 dpi and a 300 dpi scan of the same form read alike."""
    import cv2
    base = CELL_HEIGHT / cell.shape[0]
    for scale, grow in VARIANTS:
        big = cv2.resize(cell, None, fx=base * scale, fy=base * scale,
                         interpolation=cv2.INTER_AREA if base < 1 else cv2.INTER_CUBIC)
        if grow:
            big = cv2.dilate(big, np.ones((grow, grow), np.uint8))
        yield cv2.copyMakeBorder(255 - big, 24, 24, 24, 24,
                                 cv2.BORDER_CONSTANT, value=255)


CONFIDENT = 0.50       # a Tesseract reading at least this sure is trusted (measured: its
                       # wrong digits were all at 0.45 or below, its right ones 0.54+)
MODEL_MIN = 0.50       # the digit model must be at least this sure to be used
DIGIT_BOXES = set("0123456789VvXx")


def _read_char(cell: np.ndarray, whitelist: str) -> tuple[str, float]:
    """One character from a cell image (white ink on black).

    Tesseract reads typed characters perfectly and says how sure it is. When
    it is not sure (handwriting), a digit box is read by the small digit
    model instead (ocr.digits), which handles pen strokes far better. The
    engine's readings come from several enlargements, voted on; when none
    finds a character, an unrestricted reading is mapped back to a character
    the box allows (a digit box that reads "g" means 9)."""
    char, conf = _tesseract_char(cell, whitelist)
    if not set(whitelist) <= DIGIT_BOXES:
        return char, conf                            # letters: engine only
    if char and conf >= CONFIDENT:
        return char, conf
    if char in ("V", "v", "X", "x") and conf >= 0.5:
        return char, conf                            # the letter of an old NIC
    from ocr.digits import read_digit
    digit, probability = read_digit(cell)
    if digit and probability >= MODEL_MIN and digit in whitelist:
        return digit, probability * 0.9
    return char, conf


def _tesseract_char(cell: np.ndarray, whitelist: str) -> tuple[str, float]:
    votes: dict[str, list[float]] = {}
    images = list(_variants(cell))
    for image in images:
        for psm in PSMS:
            char, conf = _ocr(image, whitelist, psm)
            if char:
                votes.setdefault(char, []).append(conf)
                break
        if any(len(v) >= 2 for v in votes.values()):
            break                       # two readings agree: no need for more
    if not votes:                       # unrestricted readings, mapped back
        for image in images:
            for psm in PSMS:
                char, conf = _ocr(image, None, psm)
                char = LOOKALIKES.get(char, char)
                if char and char in whitelist:
                    votes.setdefault(char, []).append(conf * 0.8)
                    break
    if not votes:
        return "", 0.0
    best = max(votes, key=lambda c: sum(votes[c]))
    return best, sum(votes[best]) / len(votes[best])


def _clean_cell(cell: np.ndarray) -> np.ndarray:
    """What is left of the box lines inside a cell, removed: a scanned line
    is rarely a perfect 1-pixel row, so thick, wavy or dotted pieces of the
    top and bottom borders survive the shape test. Rows that are mostly ink
    and thin, wide fragments go; the digit's strokes stay."""
    import cv2
    out = (cell > 0).astype(np.uint8) * 255
    height, width = out.shape
    out[(out > 0).mean(axis=1) > 0.6] = 0                        # solid bars
    count, labels, stats, _ = cv2.connectedComponentsWithStats(out, connectivity=8)
    for i in range(1, count):
        w, h, area = (stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT],
                      stats[i, cv2.CC_STAT_AREA])
        fragment = h <= max(3, 0.1 * height) and w >= 0.3 * width
        speck = area < 0.008 * height * width
        if fragment or speck:
            out[labels == i] = 0
    return out


def _boxes_at(gray: np.ndarray, cut: int):
    """(gray, loose, cells) of the strip trimmed to its box lines, with the
    lines taken as everything darker than `cut`."""
    loose = _dark(gray, cut)
    # trim the slack the zone leaves around the boxes, down to the box lines
    # themselves: a zone a few pixels off also takes in a sliver of the
    # neighbouring row's border, and every size test below is relative to
    # the boxes' own height
    lines = _vertical_lines(loose, 0.5)
    rows, cols = np.nonzero(lines.any(axis=1))[0], np.nonzero(lines.any(axis=0))[0]
    if len(rows) == 0:
        return None
    top, bottom = rows.min(), rows.max() + 1
    left, right = cols.min(), cols.max() + 1
    gray, loose = gray[top:bottom, left:right], loose[top:bottom, left:right]
    return gray, loose, find_cells(loose)


def split_cells(strip: np.ndarray) -> list[np.ndarray]:
    """The entry ink of each box of a strip, left to right (empty boxes are
    left out). Empty list when the strip is not a row of boxes. The box lines
    of some forms are printed pale or dotted, so the lines are looked for at
    a few darkness levels and the one that finds the most boxes wins."""
    gray = _gray(strip)
    best = None
    for cut in FIND_CUTS:
        found = _boxes_at(gray, cut)
        if found is not None and (best is None or len(found[2]) > len(best[2])):
            best = found
    if best is None:
        return []
    gray, loose, cells = best
    if len(cells) < MIN_CELLS:
        return []
    ink = _strip_lines(_dark(gray, ink_level(gray, loose)))
    height = ink.shape[0]
    margin = max(2, int(height * 0.06))
    inked = []
    for x0, x1 in cells:
        cell = ink[margin:height - margin, x0 + 5:x1 - 4]
        if cell.size:
            cell = _clean_cell(cell)
        if cell.size and np.count_nonzero(cell) / cell.size >= INK_MIN:
            inked.append(cell)                         # empty boxes are skipped
    return inked


def read_comb(strip: np.ndarray, charset: str = "alnum") -> OCRResult:
    """The characters written in a strip of boxes, left to right."""
    whitelist = CHARSETS.get(charset, charset or CHARSETS["alnum"])
    inked = split_cells(strip)
    # each reading runs the OCR engine as a separate process: do several at once
    allowed = [whitelist] * len(inked)
    if charset == "nic" and inked:
        # a letter can only close an old 9-digit NIC (123456789V): every
        # earlier box is a digit, and a 12-box number has no letter at all
        digits = CHARSETS["digits"]
        allowed = [digits] * len(inked)
        if len(inked) < 12:
            allowed[-1] = whitelist
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        readings = list(pool.map(lambda pair: _read_char(*pair), zip(inked, allowed)))
    chars = [char for char, _ in readings if char]
    confs = [conf for char, conf in readings if char]
    confidence = sum(confs) / len(confs) if confs else 0.0
    return OCRResult(text="".join(chars), confidence=confidence)
