"""Reading handwritten lines (names, e-mail, branch, mobile) from form boxes.

Tesseract cannot read handwriting. This uses TrOCR (small, handwritten; MIT licence),
run with onnxruntime from the ONNX files in `models/trocr/` next to the application
(encoder_model.onnx, decoder_model.onnx, tokenizer.json). Without them `available()` is
False and the callers fall back to Tesseract, so nothing breaks.

The crop from a form is cleaned first (clean_lines): the printed box rules are removed
and only the pen strokes are kept, one image per written line, because the model reads a
single line of text at a time and is thrown by rules and neighbouring lines.

`read_line` returns the text with a confidence (the model's own certainty per character).
`rank` scores a list of candidate strings against the writing, which is how a closed
list (branch names) is matched far more reliably than free reading.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

SIZE = 384                 # the model's input is a 384x384 image
START, END = 2, 2          # decoder start / end token ids (generation_config.json)
MAX_TOKENS = 48
MODEL_DIRNAME = Path("models") / "trocr"


def model_dir() -> Path:
    """Where the model files are: DOK_TROCR_DIR, the bundle (frozen exe), or the repo."""
    override = os.environ.get("DOK_TROCR_DIR")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):               # the installer puts the model beside the exe
        beside = Path(sys.executable).resolve().parent / MODEL_DIRNAME
        if beside.exists():
            return beside
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / MODEL_DIRNAME


# ---- cleaning ---------------------------------------------------------------------------
def strokes(strip: np.ndarray) -> np.ndarray:
    """Mask (255 = ink) of what was written: box rules and cell dividers removed."""
    import cv2
    g = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY) if strip.ndim == 3 else strip
    h, w = g.shape
    level = _ink_level(g)
    dark = (g < level).astype(np.uint8) * 255
    hor = cv2.morphologyEx(dark, cv2.MORPH_OPEN,
                           cv2.getStructuringElement(cv2.MORPH_RECT, (max(25, w // 4), 1)))
    ver = cv2.morphologyEx(dark, cv2.MORPH_OPEN,
                           cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(12, int(h * 0.55)))))
    rules = cv2.dilate(cv2.bitwise_or(hor, ver), np.ones((5, 5), np.uint8))
    ink = cv2.bitwise_and(dark, cv2.bitwise_not(rules))
    return cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))   # dust


def _ink_level(gray: np.ndarray) -> int:
    """Pixels darker than this are marks (printed or written): paper-relative."""
    paper = float(np.percentile(gray, 90))
    return int(max(90, min(165, paper - 55)))


def _bands(ink: np.ndarray, min_height: int = 8) -> list[tuple[int, int]]:
    rows = (ink > 0).sum(axis=1)
    on = rows > max(2, 0.004 * ink.shape[1])
    out, start = [], None
    for y, flag in enumerate(on):
        if flag and start is None:
            start = y
        elif not flag and start is not None:
            if y - start >= min_height:
                out.append((start, y))
            start = None
    if start is not None and len(on) - start >= min_height:
        out.append((start, len(on)))
    return out


MIN_INK_PIXELS = 300       # a written line has well over a thousand ink pixels; stray marks a few hundred at most
MIN_LINE_WIDTH = 20


def clean_lines(strip: np.ndarray) -> list[np.ndarray]:
    """One white image per written line, strokes black, cropped with a margin."""
    import cv2
    ink = strokes(strip)
    lines = []
    for y0, y1 in _bands(ink):
        band = ink[max(0, y0 - 2):y1 + 2]
        xs = np.nonzero(band.any(axis=0))[0]
        if len(xs) == 0 or np.count_nonzero(band) < MIN_INK_PIXELS or xs.max() - xs.min() < MIN_LINE_WIDTH:
            continue                      # dust and box-edge debris: the model would "read" a sentence from it
        pad = max(6, int(0.3 * band.shape[0]))
        crop = band[:, max(0, xs.min() - pad):xs.max() + pad + 1]
        crop = cv2.copyMakeBorder(crop, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
        crop = cv2.dilate(crop, np.ones((2, 2), np.uint8))
        lines.append(cv2.cvtColor(255 - crop, cv2.COLOR_GRAY2BGR))
    return lines


# ---- the model -------------------------------------------------------------------------------
_lock = threading.Lock()
_state: dict = {}


def available() -> bool:
    d = model_dir()
    return all((d / f).exists() for f in ("encoder_model.onnx", "decoder_model.onnx", "tokenizer.json"))


def _load():
    if "enc" in _state:
        return _state
    import onnxruntime as ort
    from tokenizers import Tokenizer
    d = model_dir()
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = max(1, min(4, (os.cpu_count() or 2)))
    providers = ["CPUExecutionProvider"]
    _state["enc"] = ort.InferenceSession(str(d / "encoder_model.onnx"), opts, providers=providers)
    _state["dec"] = ort.InferenceSession(str(d / "decoder_model.onnx"), opts, providers=providers)
    _state["tok"] = Tokenizer.from_file(str(d / "tokenizer.json"))
    return _state


def _encode(image: np.ndarray) -> np.ndarray:
    import cv2
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB) if image.ndim == 3 else \
        cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
    rgb = cv2.resize(rgb, (SIZE, SIZE), interpolation=cv2.INTER_CUBIC)
    x = (rgb.astype(np.float32) / 255.0 - 0.5) / 0.5
    return _state["enc"].run(None, {"pixel_values": x.transpose(2, 0, 1)[None]})[0]


def _softmax(logits: np.ndarray) -> np.ndarray:
    e = np.exp(logits - logits.max(axis=-1, keepdims=True))
    return e / e.sum(axis=-1, keepdims=True)


def variants(image: np.ndarray) -> list[np.ndarray]:
    """The line at a few pen weights: one setting rarely suits every hand, the most
    confident reading wins."""
    import cv2
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    ink = 255 - gray
    out = [image]
    for k in (2, 3):
        thick = cv2.dilate(ink, np.ones((k, k), np.uint8))
        out.append(cv2.cvtColor(255 - thick, cv2.COLOR_GRAY2BGR))
    thin = cv2.erode(ink, np.ones((2, 2), np.uint8))
    out.append(cv2.cvtColor(255 - thin, cv2.COLOR_GRAY2BGR))
    return out


def read_all(image: np.ndarray) -> list[tuple[str, float]]:
    """Every reading of the line (one per pen weight), best first."""
    if not available():
        return []
    found = [_read_once(v) for v in variants(image)]
    return sorted((r for r in found if r[0]), key=lambda r: -r[1])


def read_line(image: np.ndarray, tries: bool = True) -> tuple[str, float]:
    """(text, confidence 0..1) for one cleaned line image, the best of a few pen weights;
    ("", 0) without the model."""
    if not available():
        return "", 0.0
    best = ("", 0.0)
    for variant in (variants(image) if tries else [image]):
        text, conf = _read_once(variant)
        if text and conf > best[1]:
            best = (text, conf)
    return best


def _read_once(image: np.ndarray) -> tuple[str, float]:
    with _lock:
        state = _load()
        hidden = _encode(image)
        ids, probs = [START], []
        for _ in range(MAX_TOKENS):
            logits = state["dec"].run(None, {"input_ids": np.array([ids], dtype=np.int64),
                                             "encoder_hidden_states": hidden})[0][0, -1]
            p = _softmax(logits)
            nxt = int(p.argmax())
            if nxt == END:
                break
            ids.append(nxt)
            probs.append(float(p[nxt]))
        text = state["tok"].decode(ids[1:], skip_special_tokens=True).strip()
    confidence = float(np.exp(np.mean(np.log(np.maximum(probs, 1e-6))))) if probs else 0.0
    return text, confidence


def rank(image: np.ndarray, candidates: list[str]) -> list[tuple[str, float]]:
    """Candidates best first, each with its average log-probability under the model for
    this writing (higher, closer to 0, is better). One batch through the decoder."""
    if not available() or not candidates:
        return []
    with _lock:
        state = _load()
        hidden = _encode(image)
        enc = [state["tok"].encode(c, add_special_tokens=False).ids + [END] for c in candidates]
        longest = max(len(e) for e in enc)
        batch = np.full((len(enc), longest + 1), 1, dtype=np.int64)       # 1 = pad
        for i, e in enumerate(enc):
            batch[i, 0] = START
            batch[i, 1:len(e) + 1] = e
        scores = []
        for start in range(0, len(enc), 64):
            part = batch[start:start + 64]
            logits = state["dec"].run(None, {"input_ids": part,
                                             "encoder_hidden_states": np.repeat(hidden, len(part), axis=0)})[0]
            logp = np.log(_softmax(logits) + 1e-9)
            for row, e in enumerate(enc[start:start + 64]):
                total = sum(logp[row, k, tok] for k, tok in enumerate(e))
                scores.append(float(total) / len(e))
    return sorted(zip(candidates, scores), key=lambda c: -c[1])
