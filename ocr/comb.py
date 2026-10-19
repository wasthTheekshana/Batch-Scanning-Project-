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
EXTRA_CHARSETS = {      # not offered in the zone dialog: read as a handwritten line, not cell by cell
    "email": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789@._-",
}
MIN_CELLS = 3          # fewer dividers than this: not a comb, read as a line
INK_MIN = 0.012        # a cell with less ink than this is empty
FIND_CUT = 150         # loose level used only to locate the box borders
FIND_CUTS = (FIND_CUT, 170, 190)    # pale or dotted box lines need a higher cut
INK_BELOW_BORDER = 15  # entry ink must be this much darker than the borders
INK_FLOOR, INK_CEIL = 40, 150
DEFAULT_INK = 140      # when no border level can be measured
WIDE_CUT = 175         # for writing paler than the box lines: everything dark counts, lines go by shape
LOST_FILL = 0.03       # a box with less ink than this at the ordinary cut has lost its writing
CORE_CUT = 130         # real pen strokes have pixels at least this dark; printed hint letters do not
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
    dividers = _fill_gaps(dividers, typical)
    spans = list(zip(dividers, dividers[1:]))
    return [(a, b) for a, b in spans if 0.6 * typical <= b - a <= 1.5 * typical]


def _fill_gaps(dividers: list[int], typical: int) -> list[int]:
    """Boxes are equally wide: where two dividers are about 2-3 boxes apart, the ones
    between were hidden (a pen stroke touching the line) and are put back."""
    out = [dividers[0]]
    for a, b in zip(dividers, dividers[1:]):
        k = round((b - a) / typical)
        if k >= 2 and abs((b - a) - k * typical) <= 0.25 * typical:
            out += [round(a + (b - a) * i / k) for i in range(1, k)]
        out.append(b)
    return out


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


def _strip_lines(binary: np.ndarray, vertical: bool = True) -> np.ndarray:
    import cv2
    h, w = binary.shape
    horizontal = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(5, int(w * 0.6)), 1)))
    # Tall "vertical lines" are removed on black forms; where the box lines are grey and the pen paler, the stem of
    # a 1, 4 or 7 is as tall as a divider and was being erased with it.
    lines = cv2.dilate(cv2.bitwise_or(horizontal, _vertical_lines(binary)) if vertical else horizontal,
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
NETWORK_SURE = 0.60    # the digit network at least this sure of a digit: taken, whatever Tesseract says
FAST_SURE = 0.97       # both networks at least this sure of the same digit: Tesseract is not asked
TESS_TRUSTED = 0.90    # Tesseract at least this sure of a digit (typed digits are 0.93-0.96): taken outright
NETWORK_DOUBT = 0.90   # a network this sure of a different digit makes the winner doubtful
DISAGREE_MIN = 0.70    # Tesseract this confident of a different digit makes the cell doubtful
MODEL_MIN = 0.50       # the digit model must be at least this sure to be used
DISAGREE_CONF = 0.40   # when the two readers disagree and neither is sure, the cell is reported this uncertain
SPREAD = 0.15          # a field is no more confident than its least sure box plus this
DIGIT_BOXES = set("0123456789VvXx")


def _read_char(cell: np.ndarray, whitelist: str) -> tuple[str, float]:
    """One character from a cell image (white ink on black).

    Tesseract reads typed characters perfectly and says how sure it is, but on handwritten digits it is
    right about 6 times in 10; the digit network (ocr.digits) about 97 in 100. So a digit box is read by the
    network, and Tesseract is consulted only where the network is unsure, or as a second opinion: when the two
    disagree and Tesseract is confident, the digit is kept but reported as doubtful, so the field is flagged
    for the operator. The engine's readings come from several enlargements, voted on; when none finds a
    character, an unrestricted reading is mapped back to a character the box allows (a digit box that reads
    "g" means 9)."""
    variants = list(cell) if isinstance(cell, (list, tuple)) else [cell]
    cell = variants[-1]                              # Tesseract reads the generous cut best
    if set(whitelist) <= set("0123456789"):
        # Fast path: Tesseract costs ~0.3 s a box and the two networks a few milliseconds. When both name the
        # same digit and are nearly certain, ask no one else (about 3 boxes in 4 on handwriting).
        from ocr import digits as models
        if models.cnn_available():
            first, p1 = models.read_digit(variants)
            second, p2 = models.read_digit_mlp(variants)
            if first and first == second and first in whitelist and min(p1, p2) >= FAST_SURE:
                return first, min(p1, p2) * 0.95
    char, conf = _tesseract_char(cell, whitelist)
    if not set(whitelist) <= DIGIT_BOXES:
        return char, conf                            # letters: engine only
    if char in ("V", "v", "X", "x") and conf >= 0.5:
        return char, conf                            # the letter of an old NIC
    from ocr import digits as digit_models
    digit, probability = digit_models.read_digit(variants)
    usable = bool(digit) and digit in whitelist
    if usable and digit_models.cnn_available():
        # Three readers vote: the convolutional network, the fully connected one and Tesseract. On 119 handwritten
        # digits from real bank forms the network alone was right 96% of the time, the three by majority 97.5%.
        # A digit with a confident dissenter is kept but reported as doubtful, so the operator is asked.
        if char in whitelist and conf >= TESS_TRUSTED:
            return char, conf                        # typed digits: Tesseract this sure is right
        second, second_p = digit_models.read_digit_mlp(variants)
        votes = [d for d in (digit, second if second in whitelist else "", char if char in whitelist else "") if d]
        winner = max(set(votes), key=lambda v: (votes.count(v), v == digit))
        agree = votes.count(winner)
        if agree == 3:
            return winner, max(probability, 0.9) * 0.95
        # Tesseract is the weakest reader of handwriting: only a dissenting network makes the digit doubtful.
        dissent = [(d, c) for d, c in ((digit, probability), (second, second_p)) if d and d != winner]
        if agree >= 2 and not any(c >= NETWORK_DOUBT for _, c in dissent):
            return winner, 0.8
        return winner, min(probability * 0.95, DISAGREE_CONF)
    if usable and probability >= NETWORK_SURE:
        if char and char != digit and conf >= DISAGREE_MIN:
            return digit, min(probability * 0.95, DISAGREE_CONF)         # right most of the time, but say so
        return digit, probability * 0.95
    if char and conf >= CONFIDENT:
        if usable and probability >= MODEL_MIN and digit != char:
            return char, min(conf, DISAGREE_CONF)    # unsure network against a fairly sure engine
        return char, conf
    if usable and probability >= MODEL_MIN:
        doubt = bool(char) and char != digit and conf >= 0.3         # the engine saw something else
        return digit, min(probability * 0.9, DISAGREE_CONF) if doubt else probability * 0.9
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
        top = stats[i, cv2.CC_STAT_TOP]
        # what is left of a box line lies against the cell's top or bottom edge; the bar of
        # a handwritten 5, 7 or 3 does not
        at_edge = top <= 0.1 * height or top + h >= 0.9 * height
        fragment = h <= max(3, 0.1 * height) and w >= 0.3 * width and at_edge
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
    return gray, loose, find_cells(loose), (top, bottom, left, right)


BLUE_MIN = 28          # how much bluer than red a pixel is for it to count as blue pen
PEN_MIN_FRACTION = 0.004   # a strip needs this much blue before it is read by colour


def _pen_ink(color: np.ndarray, gray: np.ndarray) -> np.ndarray | None:
    """Mask (255 = ink) of the writing when it is in blue (or other coloured) pen on a black
    form. Colour separates pen from printed box lines even where they overlap, which a
    grey-level cut cannot (the top bar of a 5 lying on the box line). None when the strip has
    no coloured writing, so black-pen forms use the grey-level way."""
    import cv2
    b, g, r = (color[..., i].astype(np.int16) for i in range(3))
    mask = (((b - r) > BLUE_MIN) & (gray < 200)).astype(np.uint8) * 255
    if np.count_nonzero(mask) < PEN_MIN_FRACTION * mask.size:
        return None
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8))


def cell_variants(strip: np.ndarray) -> list[list[np.ndarray]]:
    """The entry ink of each box of a strip, left to right (empty boxes are
    left out), each box as one or more images made with different ink cuts. Empty list when the strip is not a row of boxes. The box lines
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
    gray, loose, cells, (top, bottom, left, right) = best
    if len(cells) < MIN_CELLS:
        return []
    pen = _pen_ink(strip[top:bottom, left:right], gray) if strip.ndim == 3 else None
    if pen is not None:
        spans = [_cell_images(pen, cells)]
    else:
        level = ink_level(gray, loose)
        if level == DEFAULT_INK:
            # Black borders (or none measurable): everything dark counts as ink and the lines go by shape.
            spans = [_cell_images(_strip_lines(_dark(gray, DEFAULT_INK)), cells)]
        else:
            spans = [_cell_images(_strip_lines(_dark(gray, level), vertical=False), cells)]
            # Writing as pale as, or paler than, the box lines (soft pencil, a light pen on grey lines): the
            # darkness cut that spares the lines also loses the writing, and digits come out as fragments. Count
            # everything dark as ink and let the lines be removed by shape, if that finds clearly more writing.
            wide = _cell_images(_strip_lines(_dark(gray, WIDE_CUT), vertical=False), cells)
            # ...but only in boxes whose writing has a dark core: the printed hint letters of a date box
            # ("D D M M Y Y Y Y") are as pale as pencil and must not be read as writing.
            core = _cell_images(_strip_lines(_dark(gray, CORE_CUT), vertical=False), cells)
            wide = [_attached(w, c) if c is not None and w is not None else None for w, c in zip(wide, core)]
            spans = [[_better(p_, w_) for p_, w_ in zip(spans[0], wide)]]
    variants = []
    for k in range(len(cells)):
        found = [s_[k] for s_ in spans if s_[k] is not None]
        if found:
            variants.append(found)
    return variants


def _attached(wide: np.ndarray, core: np.ndarray) -> np.ndarray:
    """The generous cut's ink, minus what is not joined to the dark core of a stroke: a pale printed hint letter
    sitting beside a digit is dropped, the pale part of the digit's own stroke stays."""
    import cv2
    near = cv2.dilate(core, np.ones((3, 3), np.uint8), iterations=3)
    count, labels = cv2.connectedComponents((wide > 0).astype(np.uint8), connectivity=8)
    keep = np.zeros_like(wide)
    for i in range(1, count):
        part = labels == i
        if (near[part] > 0).any():
            keep[part] = 255
    return keep


def _fill(image: np.ndarray | None) -> float:
    return np.count_nonzero(image) / image.size if image is not None else 0.0


def _better(plain: np.ndarray | None, wide: np.ndarray | None) -> np.ndarray | None:
    """One box's ink: the ordinary cut's, unless that lost the writing (almost nothing left: the stroke is paler
    than the box lines). Measured: on pale-pencil scans the ordinary cut keeps 0-2.6% of a box, on a normal
    scan at least 5.9%."""
    if wide is None:
        return plain
    return wide if plain is None or _fill(plain) < LOST_FILL else plain


def _count(images: list[np.ndarray | None]) -> int:
    return sum(1 for c in images if c is not None)


def _coverage(images: list[np.ndarray | None]) -> float:
    """How much of a box the writing covers on average (over the boxes that have any)."""
    found = [np.count_nonzero(c) / c.size for c in images if c is not None]
    return sum(found) / len(found) if found else 0.0


def split_cells(strip: np.ndarray) -> list[np.ndarray]:
    """The entry ink of each box of a strip, left to right (empty boxes are left out); one image per box."""
    return [v[0] for v in cell_variants(strip)]


def _cell_images(ink: np.ndarray, cells: list[tuple[int, int]]) -> list[np.ndarray | None]:
    """The cleaned ink of every box (None where the box is empty)."""
    height = ink.shape[0]
    margin = max(2, int(height * 0.06))
    out: list[np.ndarray | None] = []
    for x0, x1 in cells:
        cell = ink[margin:height - margin, x0 + 5:x1 - 4]
        if cell.size:
            cell = _clean_cell(cell)
        out.append(cell if cell.size and np.count_nonzero(cell) / cell.size >= INK_MIN else None)
    return out


def read_comb(strip: np.ndarray, charset: str = "alnum") -> OCRResult:
    """The characters written in a strip of boxes, left to right."""
    whitelist = CHARSETS.get(charset) or EXTRA_CHARSETS.get(charset) or charset or CHARSETS["alnum"]
    inked = cell_variants(strip)
    # each reading runs the OCR engine as a separate process: do several at once
    allowed = [whitelist] * len(inked)
    if charset == "nic" and inked:
        # a letter can only close an old 9-digit NIC (123456789V): every
        # earlier box is a digit, and a 12-box number has no letter at all
        digits = CHARSETS["digits"]
        allowed = [digits] * len(inked)
        if len(inked) == 10:
            allowed[-1] = "VX"              # nine digits and the letter of an old NIC
        elif len(inked) < 12:
            allowed[-1] = whitelist
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        readings = list(pool.map(lambda pair: _read_char(*pair), zip(inked, allowed)))
    chars = [char for char, _ in readings if char]
    confs = [conf for char, conf in readings if char]
    confidence = sum(confs) / len(confs) if confs else 0.0
    if confs:
        confidence = min(confidence, min(confs) + SPREAD)       # one doubtful box makes the number doubtful
    return OCRResult(text="".join(chars), confidence=confidence)
