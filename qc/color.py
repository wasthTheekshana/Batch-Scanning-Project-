"""QC Stage 4 — Colour & contrast fix (HLD §5).

Background whitening → CLAHE (LAB L channel) → despeckle, then colour
mode enforcement. Michelson contrast < 0.3 after fixing → −10.
"""
from __future__ import annotations

import cv2
import numpy as np

from imaging.enhance import (apply_clahe, contrast, despeckle, to_color_mode,
                             whiten_background)
from imaging.enhance import CLAHE_BELOW
from qc.base import BaseQCStage, QCStageResult

MICHELSON_MIN = 0.3


def michelson_contrast(img: np.ndarray) -> float:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    lo, hi = np.percentile(gray, (2, 98))
    lo, hi = float(lo), float(hi)
    if hi + lo == 0:
        return 0.0
    return (hi - lo) / (hi + lo)


class ColorStage(BaseQCStage):
    @property
    def name(self) -> str:
        return "color"

    def run(self, image: np.ndarray, context: dict) -> QCStageResult:
        if context.get("enhanced"):
            # the enhance step already cleaned this page: measure it, do not run the
            # same filters over it again (each pass costs sharpness)
            out, fixed = image, False
        else:
            out = whiten_background(image)
            if contrast(out) < CLAHE_BELOW:
                out = apply_clahe(out)
            out = despeckle(out, int(context.get("dpi", 300)))
            fixed = True
        mode = context.get("color_mode")
        if mode:
            out, fixed = to_color_mode(out, mode, int(context.get("dpi", 300))), True

        contrast_now = michelson_contrast(out)
        deduction, flags = 0, []
        if contrast_now < MICHELSON_MIN:
            deduction = 10
            flags.append("low_contrast")

        return QCStageResult(stage=self.name, deduction=deduction, fixed=fixed,
                             flags=flags, image=out if fixed else None,
                             detail={"michelson_contrast": round(contrast_now, 3)})
