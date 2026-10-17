"""Named-zone field extraction (HLD §4 Layer 5).

Zones are rectangles configured per project; each yields one metadata
field. Extracted text is cleaned with the zone's regex (first group or
whole match) and returned with the OCR confidence.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable

import numpy as np

from ocr.tesseract_engine import OCRResult, ocr_page

log = logging.getLogger(__name__)


@dataclass
class FieldValue:
    value: str
    confidence: float


def _extract_match_value(pattern: str, text: str) -> str | None:
    """Extract value from regex match: first capture group if present,
    otherwise the entire match. Returns None if no match, or the extracted
    string (possibly empty for matched-but-empty captures)."""
    m = re.search(pattern, text)
    return (m.group(1) if m.lastindex else m.group(0)) if m else None


def scaled_rect(zone: dict, img_w: int, img_h: int) -> tuple[int, int, int, int]:
    """Zone rect in this image's pixels. Zones drawn on a sample of a
    different resolution carry ref_w/ref_h and are scaled proportionally
    so extraction is resolution-independent."""
    x, y, w, h = int(zone["x"]), int(zone["y"]), int(zone["w"]), int(zone["h"])
    ref_w, ref_h = zone.get("ref_w"), zone.get("ref_h")
    if ref_w and ref_h and (int(ref_w) != img_w or int(ref_h) != img_h):
        sx, sy = img_w / int(ref_w), img_h / int(ref_h)
        x, y, w, h = round(x * sx), round(y * sy), round(w * sx), round(h * sy)
    return x, y, w, h


MIN_STRIP_HEIGHT = 48     # pixels: shorter crops are enlarged before OCR


def prepare_strip(strip: np.ndarray) -> np.ndarray:
    """A crop from a form, made readable: the printed box lines that run
    through or beside the entry are erased (they merge with letters and
    swallow the first or last character), and a thin crop is enlarged (the
    engine misses text on images only ~30 px high)."""
    import cv2
    gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY) if strip.ndim == 3 else strip
    if gray.size == 0:
        return strip
    h, w = gray.shape
    marks = (gray < 200).astype(np.uint8) * 255
    horizontal = cv2.morphologyEx(
        marks, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(15, int(w * 0.1)), 1)))
    vertical = cv2.morphologyEx(
        marks, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(10, int(h * 0.9)))))
    core = cv2.bitwise_or(horizontal, vertical) > 0
    lines = cv2.dilate(core.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    cleaned = gray.copy()
    if lines.any():
        # erase only what is as light as the printed lines: the entry itself
        # (darker ink, e.g. the tail of a "y" crossing a box edge) is kept.
        # The line's own darkness is measured on the line pixels, not on the
        # thickened mask (a thin line is mostly white paper in that mask)
        # (a turned or resampled line is dark in places and pale in others, so
        # the darkest tenth of its pixels sets the level, not the median)
        border = int(np.percentile(gray[core], 10))
        cleaned[lines & (gray >= border - 15)] = 255
    if h < MIN_STRIP_HEIGHT:
        scale = MIN_STRIP_HEIGHT / h
        cleaned = cv2.resize(cleaned, None, fx=scale, fy=scale,
                             interpolation=cv2.INTER_CUBIC)
    # the engine drops characters that touch the image edge
    cleaned = cv2.copyMakeBorder(cleaned, 12, 12, 12, 12, cv2.BORDER_CONSTANT,
                                 value=255)
    return cleaned


FIXERS = {"name": "fix_name", "email": "fix_email", "mobile": "fix_mobile", "nic": "fix_nic"}


TYPED_SURE = 0.80       # the ordinary reader at least this sure of a line: typed or printed text (typed: 0.82-0.94, real handwriting: at most 0.77)


def _read_typed(strip: np.ndarray, zone: dict, run_ocr) -> OCRResult:
    """A cropped strip is a line (or a few), not a page: read it with the ordinary OCR engine."""
    lines = int(zone.get("lines") or 0)
    if not lines:
        lines = 1 if strip.shape[0] <= 0.2 * strip.shape[1] else 2
    result = run_ocr(prepare_strip(strip), psm=7 if lines == 1 else 6,
                     min_conf=0 if lines == 1 else 45)
    printable = re.sub(r"[^\x20-\x7e\n]", "", result.text)
    if lines > 1:      # keep real lines, drop the box-edge debris
        printable = " ".join(
            ln.strip() for ln in printable.splitlines()
            if re.search(r"[A-Za-z0-9]{2}", ln))
    return OCRResult(text=printable, confidence=result.confidence)


def _clearly_typed(result: OCRResult, zone: dict) -> bool:
    """Did the ordinary reader read this strip with confidence, and is what it read the right
    shape for the field (its clean-up pattern finds a value)?"""
    if not result.text.strip() or result.confidence < TYPED_SURE:
        return False
    pattern = zone.get("regex")
    if not pattern:
        return True
    found = _extract_match_value(pattern, result.text.strip())
    return bool(found and found.strip())        # an empty box can "match" with just a space


def read_handwritten(strip: np.ndarray, zone: dict) -> OCRResult | None:
    """A handwritten line (or lines) read with the handwriting model, then corrected for the
    kind of field it is (name, e-mail, mobile, branch ...). None when the model is not
    installed, so the caller reads it the older way."""
    from ocr import fieldfix, handwriting
    if not handwriting.available():
        return None
    lines = handwriting.clean_lines(strip)
    if not lines:
        return OCRResult(text="", confidence=0.0)
    kind = zone.get("kind")
    if kind == "branch":
        text, conf = handwriting.read_line(lines[0])
        ranked = handwriting.rank(lines[0], fieldfix.branches()) if fieldfix.branches() else None
        value, factor = fieldfix.fix_branch(text, ranked)
        return OCRResult(text=value, confidence=min(conf + 0.3, 1.0) * factor if ranked else conf * factor)
    read = [handwriting.read_line(line) for line in lines]
    fixer = getattr(fieldfix, FIXERS.get(kind, ""), None)
    if kind == "email":
        # the @ is the glyph the model misreads most: rank repaired addresses against the writing
        text, conf = read[0] if read else ("", 0.0)
        candidates = fieldfix.email_candidates(*[t for t, _ in handwriting.read_all(lines[0])])
        if candidates:
            ranked = sorted(handwriting.rank(lines[0], candidates),
                            key=lambda c: -(c[1] + fieldfix.email_prior(c[0])))
            value, factor = fieldfix.fix_email(ranked[0][0])
            return OCRResult(text=value, confidence=max(conf, 0.5) * factor)
    if kind == "name":              # clean each line on its own: a box edge sits at a line's end
        parts = []
        for (t, _), line in zip(read, lines):
            variants = fieldfix.name_variants(t)
            if len(variants) > 1:
                ranked = handwriting.rank(line, variants)             # the one that fits the strokes
                variants = [max(ranked, key=lambda c: c[1] + fieldfix.name_prior(c[0]))[0]]
            parts += variants[:1] or [fieldfix.fix_name(t, snap=False)[0]]
        text = " ".join(fieldfix.fix_name(p, snap=False)[0] for p in parts if p)
    else:
        text = " ".join(t for t, _ in read if t)
    conf = sum(c for _, c in read) / len(read)
    if fixer and kind != "name":
        text, factor = fixer(text)
        conf *= factor
    return OCRResult(text=text, confidence=conf)


def extract_fields(img: np.ndarray, zones: list[dict], lang: str = "eng",
                   ocr_fn: Callable[[np.ndarray], OCRResult] | None = None,
                   words: list[dict] | None = None
                   ) -> dict[str, FieldValue]:
    """zones: [{'name': 'invoice_no', 'x':..,'y':..,'w':..,'h':..,
    'regex': optional, 'ref_w'/'ref_h': optional sample dimensions}].
    ocr_fn is injectable for tests. When the zones carry `anchors` (printed
    words of the blank form) and `words` (the page's OCR words) are given,
    the zones are first lined up with this scan (ocr.align), so a form that
    was printed smaller or fed in crooked is still read in the right boxes."""
    run_ocr = ocr_fn or (lambda region, psm=None, min_conf=0,
                         line_min_conf=0: ocr_page(
        region, lang=lang, psm=psm, min_conf=min_conf,
        line_min_conf=line_min_conf))
    h, w = img.shape[:2]
    fields: dict[str, FieldValue] = {}
    anchors = next((z["anchors"] for z in zones if z.get("anchors")), None)
    if anchors and words:
        from ocr.align import (STRAIGHT_ENOUGH, dewarp, estimate_transform,
                               map_zone, rotation_degrees)
        matrix = estimate_transform(anchors, words, tolerance=max(8.0, 0.006 * w))
        ref = next(((z["ref_w"], z["ref_h"]) for z in zones
                    if z.get("ref_w") and z.get("ref_h")), None)
        if matrix is not None and ref is not None:
            if abs(rotation_degrees(matrix)) < STRAIGHT_ENOUGH:
                # straight: move the regions onto the scan, leave its pixels alone
                zones = [map_zone(z, matrix) if all(k in z for k in "xywh") else z
                         for z in zones]
            else:
                img = dewarp(img, matrix, *ref)     # turned: straighten onto the template
                h, w = img.shape[:2]
    for zone in zones:
        if not all(k in zone for k in ("x", "y", "w", "h")):
            continue  # not yet positioned (e.g. a fresh template zone)
        zx, zy, zw, zh = scaled_rect(zone, w, h)
        x0 = max(0, min(zx, w - 1))
        y0 = max(0, min(zy, h - 1))
        x1 = max(x0 + 1, min(x0 + zw, w))
        y1 = max(y0 + 1, min(y0 + zh, h))
        strip = img[y0:y1, x0:x1]
        hand = typed = None
        if (zone.get("hand") or zone.get("charset") == "email") and ocr_fn is None:
            if not zone.get("comb"):
                # typed or printed entries are read better by the ordinary reader, and say so
                # with a high confidence; only what is not clearly typed goes to the handwriting model
                typed = _read_typed(strip, zone, run_ocr)
                if not _clearly_typed(typed, zone):
                    typed = None
            if typed is None:
                hand = read_handwritten(strip, zone)
        if typed is not None:
            result = typed
        elif hand is not None:
            result = hand
        elif zone.get("comb"):
            # one character per box (account no., NIC, date of birth)
            from ocr.comb import read_comb
            result = read_comb(strip, zone.get("charset") or "alnum")
        elif ocr_fn is not None:
            result = ocr_fn(strip)
        else:
            result = _read_typed(strip, zone, run_ocr)
        value = result.text.strip()
        if zone.get("regex"):
            extracted = _extract_match_value(zone["regex"], value)
            value = extracted if extracted is not None else ""
        fields[zone["name"]] = FieldValue(value=value,
                                          confidence=result.confidence)
    return fields


def extract_fields_from_text(text: str, confidence: float,
                             zones: list[dict]) -> dict[str, FieldValue]:
    """Full-page mode: locate each field's value in already-OCR'd page
    text via its label-anchored regex, instead of cropping a fixed zone.
    A zone with no regex is skipped — unanchored, page-wide matching has
    no way to know which part of the page belongs to it."""
    fields: dict[str, FieldValue] = {}
    for zone in zones:
        pattern = zone.get("regex")
        if not pattern:
            log.warning("zone %r has no regex; skipped in full-page mode", zone.get("name"))
            continue
        value = _extract_match_value(pattern, text)
        if value is not None:
            fields[zone["name"]] = FieldValue(value=value, confidence=confidence)
        else:
            fields[zone["name"]] = FieldValue(value="", confidence=0.0)
    return fields
