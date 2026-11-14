"""Does image enhancement make real scans clearer or worse?

Runs real scanner pages through the application's own enhancement chain and
measures, stage by stage, how readable they are:

    words      words of 3+ letters/digits the OCR engine finds
    sure       of those, how many it is 80%+ sure of (the readability number)
    conf       mean confidence of those words
    sharp      Laplacian variance (what the QC blur check uses)
    noise      spread of the paper background (lower = cleaner)
    ink        darkest strokes (lower = blacker text)

    python scripts/quality_loop.py DIR_OR_FILES...  [--max 6] [--variants raw,pipeline,...]
    python scripts/quality_loop.py --staging 8      # the 8 newest pages in the app's staging folder

Variants: raw (as scanned), enhance (the enhance step only), pipeline (enhance
then QC: what the application delivers today), and the experimental ones named
in VARIANTS below. The RED signal is a variant whose `sure` is LOWER than raw:
enhancement then hurts reading. Crops of the same text at 100% are written to a
temp folder (printed) so the difference can be looked at.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np

from imaging.crop import auto_crop
from imaging.deskew import deskew
from imaging.enhance import apply_profile
from ocr.orientation import upright
from ocr.tesseract_engine import ocr_page
from qc.pipeline import run_qc

PROFILE = {"deskew": True, "crop": True, "clahe": True, "despeckle": True,
           "whiten_bg": True, "target_dpi": 300}
QC = {"enabled": True, "stages": ["punch_hole", "rotation", "margin", "color", "advanced"],
      "pass_score": 85, "blur_warn_threshold": 80, "blur_fail_threshold": 30,
      "dpi_warn": 200, "dpi_fail": 150}


def to_gray(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img


def enhance_step(img, profile=PROFILE):
    """What pipeline/job_queue.py's enhance worker does to a page."""
    out = img
    if profile.get("deskew"):
        out, _ = deskew(out)
    if profile.get("crop"):
        out = auto_crop(out)
    return apply_profile(out, profile)


def pipeline(img, dpi=300):
    """Enhance step, then QC (its colour/advanced stages filter again)."""
    out = enhance_step(img)
    fixed, _verdict = run_qc(out, QC, {"dpi": dpi, "enhanced": True})   # as pipeline/job_queue.py does
    return fixed


# Experimental variants are registered here by name (filled in as they are tried).
def without(**off):
    """The enhance step with some filters switched off (an ablation test)."""
    profile = {**PROFILE, **{k: False for k in off}}
    return lambda img, dpi: enhance_step(img, profile)


VARIANTS = {
    "raw": lambda img, dpi: img,
    "enhance": lambda img, dpi: enhance_step(img),
    "pipeline": pipeline,
    # one thing at a time, from the enhance step
    "no_despeckle": without(despeckle=True),
    "no_clahe": without(clahe=True),
    "no_whiten": without(whiten_bg=True),
    "no_deskew": without(deskew=True),
    "geometry_only": without(clahe=True, despeckle=True, whiten_bg=True),   # deskew + crop only
    "filters_only": without(deskew=True, crop=True),                         # no geometry change
}


def _pipe_without(*stages):
    def run(img, dpi):
        qc = {**QC, "stages": [x for x in QC["stages"] if x not in stages]}
        fixed, _ = run_qc(enhance_step(img), qc, {"dpi": dpi, "enhanced": True})
        return fixed
    return run


def _median_only(img, dpi):
    return cv2.medianBlur(img, 3)


def _open_only(img, dpi):
    return cv2.morphologyEx(img, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2)))


def _hw_bw(img, dpi):
    """What a scanner's own 1-bit mode does: one fixed threshold for the whole page,
    then the application's pipeline."""
    gray = to_gray(img)
    return pipeline(np.where(gray > 128, 255, 0).astype(np.uint8), dpi)


def _sw_bw(img, dpi):
    """What the application does now for a black-and-white project: scan grayscale,
    clean it, then binarize locally (imaging.enhance.binarize)."""
    from imaging.enhance import binarize
    return binarize(pipeline(to_gray(img), dpi), dpi)


VARIANTS.update({
    "hw_bw": _hw_bw,
    "sw_bw": _sw_bw,
    "median_only": _median_only,              # the two halves of despeckle, alone, on the raw scan
    "open_only": _open_only,
    "qc_no_color": _pipe_without("color"),    # the full pipeline minus one QC stage
    "qc_no_advanced": _pipe_without("advanced"),
    "qc_no_color_adv": _pipe_without("color", "advanced"),
    "qc_no_rotation": _pipe_without("rotation"),
})


FAST = False          # --fast: skip the (slow) OCR, measure only sharpness / noise / ink


def measure(img) -> dict:
    gray = to_gray(img)
    result = ocr_page(gray) if not FAST else None
    words = [w for w in ((result.words if result else None) or []) if len(str(w["text"]).strip()) >= 3
             and any(ch.isalnum() for ch in str(w["text"]))]
    confs = [w.get("conf", 0) for w in words]
    paper = gray[gray > 200]
    return {
        "words": len(words),
        "sure": sum(1 for c in confs if c >= 80),
        "conf": float(np.mean(confs)) if confs else 0.0,
        "sharp": float(cv2.Laplacian(gray, cv2.CV_64F).var()),
        "noise": float(paper.std()) if paper.size else 0.0,
        "ink": float(np.percentile(gray, 2)),
    }


def crop_of_text(gray, size=(520, 260)):
    """A 100% crop of the densest text region (same place every variant: found on the first)."""
    h, w = gray.shape
    ink = (gray < 128).astype(np.uint8)
    best, at = -1, (0, 0)
    step = 80
    for y in range(0, max(1, h - size[1]), step):
        for x in range(0, max(1, w - size[0]), step):
            s = int(ink[y:y + size[1], x:x + size[0]].sum())
            if s > best:
                best, at = s, (x, y)
    return at


def load_pages(args) -> list[Path]:
    paths: list[Path] = []
    if args.staging:
        staging = Path(os.environ["LOCALAPPDATA"]) / "DOKScanner" / "staging"
        files = [p for p in staging.glob("*_p0*.png")]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        paths += list(reversed(files[:args.staging]))
    for item in args.inputs:
        p = Path(item)
        paths += sorted(p.glob("*.png")) + sorted(p.glob("*.bmp")) if p.is_dir() else [p]
    return paths[:args.max] if args.max else paths


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("inputs", nargs="*")
    ap.add_argument("--staging", type=int, default=0, help="use the N newest pages of the app's staging folder")
    ap.add_argument("--max", type=int, default=8)
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--variants", default="raw,enhance,pipeline")
    ap.add_argument("--fast", action="store_true", help="skip the OCR: sharpness, noise and ink only")
    args = ap.parse_args(argv)
    global FAST
    FAST = args.fast
    names = [v.strip() for v in args.variants.split(",")]
    unknown = [n for n in names if n not in VARIANTS]
    if unknown:
        raise SystemExit(f"unknown variant(s) {unknown}; known: {list(VARIANTS)}")
    pages = load_pages(args)
    if not pages:
        raise SystemExit("no pages given")
    out_dir = Path(tempfile.mkdtemp(prefix="quality_"))
    totals = {n: {k: [] for k in ("words", "sure", "conf", "sharp", "noise", "ink")} for n in names}
    print(f"{'page':14} {'variant':10} {'words':>6} {'sure':>5} {'conf':>6} {'sharp':>8} {'noise':>6} {'ink':>5}")
    for page in pages:
        raw = cv2.imread(str(page), cv2.IMREAD_UNCHANGED)
        if raw is None:
            print(f"{page.name[-14:]:14} unreadable")
            continue
        if not FAST:
            _turn, raw, _r = upright(raw, ocr_page(to_gray(raw)), ocr_page)   # upright, as the pipeline does
        at = None
        for name in names:
            img = VARIANTS[name](raw, args.dpi)
            m = measure(img)
            for k, v in m.items():
                totals[name][k].append(v)
            print(f"{page.name[-14:]:14} {name:10} {m['words']:6d} {m['sure']:5d} {m['conf']:6.1f} "
                  f"{m['sharp']:8.0f} {m['noise']:6.1f} {m['ink']:5.0f}")
            gray = to_gray(img)
            if at is None:
                at = crop_of_text(gray)
            x, y = at
            cv2.imwrite(str(out_dir / f"{page.stem[-8:]}_{name}.png"), gray[y:y + 260, x:x + 520])
    print("\nAVERAGE over", len(pages), "page(s)")
    base = np.mean(totals[names[0]]["sure"]) if totals[names[0]]["sure"] else 0
    for name in names:
        t = {k: float(np.mean(v)) if v else 0.0 for k, v in totals[name].items()}
        flag = "" if name == names[0] else ("   <-- WORSE than raw (RED)" if t["sure"] < base * 0.97 else "")   # >3% fewer sure words
        print(f"{'':14} {name:10} {t['words']:6.0f} {t['sure']:5.0f} {t['conf']:6.1f} "
              f"{t['sharp']:8.0f} {t['noise']:6.1f} {t['ink']:5.0f}{flag}")
    print("\ncrops at 100% in", out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
