"""QC Stage 2 — Orientation & rotation fix (HLD §5).

Coarse 0/90/180/270 orientation via Tesseract OSD, fine skew via
HoughLinesP. Deductions: −20 unresolvable, −5 low OSD confidence,
−5 residual skew > 2° (Appendix A).
"""
from __future__ import annotations

import cv2
import numpy as np

from imaging.deskew import deskew, detect_skew_angle
from qc.base import BaseQCStage, QCStageResult

OSD_CONFIDENCE_GATE = 2.0


def _rotate_90s(img: np.ndarray, angle: int) -> np.ndarray:
    rotations = {90: cv2.ROTATE_90_COUNTERCLOCKWISE,
                 180: cv2.ROTATE_180,
                 270: cv2.ROTATE_90_CLOCKWISE}
    return cv2.rotate(img, rotations[angle]) if angle in rotations else img


class RotationStage(BaseQCStage):
    @property
    def name(self) -> str:
        return "rotation"

    def run(self, image: np.ndarray, context: dict) -> QCStageResult:
        # osd_fn injectable for tests; default = real Tesseract OSD
        osd_fn = context.get("osd_fn")
        if osd_fn is None:
            from ocr.tesseract_engine import TesseractNotFound, ocr_osd

            def osd_fn(img):
                try:
                    return ocr_osd(img)
                except TesseractNotFound:
                    return None

        deduction, flags, detail = 0, [], {}
        out = image

        osd = osd_fn(image)
        if osd is None:
            deduction += 20
            flags.append("orientation_unresolvable")
            detail["osd"] = "unavailable"
        else:
            detail["osd_rotate"] = osd["rotate"]
            detail["osd_conf"] = osd["orientation_conf"]
            if osd["rotate"]:
                out = _rotate_90s(out, osd["rotate"])
            if osd["orientation_conf"] < OSD_CONFIDENCE_GATE:
                deduction += 5
                flags.append("low_osd_confidence")

        out, applied = deskew(out)
        residual = detect_skew_angle(out)
        detail["fine_skew_applied"] = round(applied, 2)
        detail["residual_skew"] = round(residual, 2)
        if abs(residual) > 2.0:
            deduction += 5
            flags.append("residual_skew")

        changed = (out is not image)
        return QCStageResult(stage=self.name, deduction=deduction,
                             fixed=changed, flags=flags, detail=detail,
                             image=out if changed else None)
