"""pytesseract wrapper with confidence scoring (HLD §4 Layer 5).

The packaged EXE bundles tesseract.exe + tessdata; in development the
system Tesseract on PATH is used. All entry points degrade cleanly with
TesseractNotFound so the pipeline can keep moving pages without OCR.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


class TesseractNotFound(Exception):
    """Tesseract binary is not available on this machine."""


@dataclass
class OCRResult:
    text: str
    confidence: float  # 0.0 – 1.0 mean word confidence


def _pytesseract():
    try:
        import pytesseract
        return pytesseract
    except ImportError as e:
        raise TesseractNotFound(str(e)) from e


def is_available() -> bool:
    try:
        _pytesseract().get_tesseract_version()
        return True
    except Exception:
        return False


def ocr_page(img: np.ndarray, lang: str = "eng",
             psm: int | None = None, min_conf: float = 0,
             line_min_conf: float = 0) -> OCRResult:
    """Full-page OCR with mean word confidence. psm selects the page
    segmentation mode (7: one line of text, 6: a block of lines); the
    default suits a whole page and mangles a thin cropped strip. Words the
    engine is less sure of than min_conf (box edges read as symbols) are
    dropped, and so are whole lines whose average confidence is under
    line_min_conf (a box's debris read as a line of its own)."""
    pt = _pytesseract()
    try:
        data = pt.image_to_data(img, lang=lang, output_type=pt.Output.DICT,
                                config="" if psm is None else f"--psm {psm}")
    except pt.TesseractNotFoundError as e:
        raise TesseractNotFound(str(e)) from e
    lines: dict[tuple, list[tuple[str, float]]] = {}
    for i, (word, conf) in enumerate(zip(data["text"], data["conf"])):
        if word.strip() and float(conf) >= max(0, min_conf):
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            lines.setdefault(key, []).append((word, float(conf)))
    words, confs = [], []
    for entries in lines.values():
        if sum(c for _, c in entries) / len(entries) < line_min_conf:
            continue
        for word, conf in entries:
            words.append(word)
            confs.append(conf)
    text = " ".join(words)
    confidence = (sum(confs) / len(confs) / 100.0) if confs else 0.0
    return OCRResult(text=text, confidence=confidence)


def ocr_text(img: np.ndarray, lang: str = "eng") -> str:
    """Plain text convenience used by zone extraction."""
    return ocr_page(img, lang=lang).text


def ocr_osd(img: np.ndarray) -> dict:
    """Orientation and script detection for the QC rotation stage.
    Returns {'rotate': 0|90|180|270, 'orientation_conf': float}."""
    pt = _pytesseract()
    try:
        osd = pt.image_to_osd(img, output_type=pt.Output.DICT)
        return {"rotate": int(osd.get("rotate", 0)),
                "orientation_conf": float(osd.get("orientation_conf", 0.0))}
    except pt.TesseractNotFoundError as e:
        raise TesseractNotFound(str(e)) from e
    except pt.TesseractError:
        # OSD fails on pages with too little text — treat as "no rotation
        # detected, zero confidence" so the stage flags a WARN.
        return {"rotate": 0, "orientation_conf": 0.0}
