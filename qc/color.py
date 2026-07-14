"""QC Stage 4 — Colour & contrast fix (HLD §5).

Background whitening → CLAHE (LAB L channel) → despeckle, then colour
mode enforcement. Michelson contrast < 0.3 after fixing → −10.
"""
from __future__ import annotations

import cv2
import numpy as np

from imaging.enhance import apply_clahe, despeckle, to_color_mode, whiten_background
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
        out = whiten_background(image)
        out = apply_clahe(out)
        out = despeckle(out)
        mode = context.get("color_mode")
        if mode:
            out = to_color_mode(out, mode)

        contrast = michelson_contrast(out)
        deduction, flags = 0, []
        if contrast < MICHELSON_MIN:
            deduction = 10
            flags.append("low_contrast")

        return QCStageResult(stage=self.name, deduction=deduction, fixed=True,
                             flags=flags, image=out,
                             detail={"michelson_contrast": round(contrast, 3)})
