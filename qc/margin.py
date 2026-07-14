"""QC Stage 3 — Margin & crop fix (HLD §5).

Auto-crop to content bbox + 40px safe margin, optional canvas
normalisation to a batch-wide size, and the DPI gate:
<200 DPI → −20 WARN, <150 DPI → −30 auto-FAIL. Severe crop asymmetry
(>20% one side) → −10.
"""
from __future__ import annotations

import cv2
import numpy as np

from imaging.crop import content_bbox
from qc.base import BaseQCStage, QCStageResult

SAFE_MARGIN_PX = 40


class MarginStage(BaseQCStage):
    @property
    def name(self) -> str:
        return "margin"

    def run(self, image: np.ndarray, context: dict) -> QCStageResult:
        cfg = context.get("config", {})
        dpi = int(context.get("dpi", 300))
        dpi_warn = int(cfg.get("dpi_warn", 200))
        dpi_fail = int(cfg.get("dpi_fail", 150))

        deduction, flags, detail = 0, [], {"dpi": dpi}
        auto_fail = False

        if dpi < dpi_fail:
            deduction += 30
            auto_fail = True
            flags.append("dpi_below_fail")
        elif dpi < dpi_warn:
            deduction += 20
            flags.append("dpi_below_warn")

        out = image
        bbox = content_bbox(image)
        if bbox:
            x, y, w, h = bbox
            ih, iw = image.shape[:2]
            margins = {"left": x, "top": y,
                       "right": iw - (x + w), "bottom": ih - (y + h)}
            detail["margins"] = margins
            horiz, vert = margins["left"] + margins["right"], margins["top"] + margins["bottom"]
            if (horiz and abs(margins["left"] - margins["right"]) / max(horiz, 1) > 0.2) or \
               (vert and abs(margins["top"] - margins["bottom"]) / max(vert, 1) > 0.2):
                deduction += 10
                flags.append("crop_asymmetry")

            x0 = max(0, x - SAFE_MARGIN_PX)
            y0 = max(0, y - SAFE_MARGIN_PX)
            x1 = min(iw, x + w + SAFE_MARGIN_PX)
            y1 = min(ih, y + h + SAFE_MARGIN_PX)
            out = image[y0:y1, x0:x1]

        canvas = context.get("target_canvas")  # (h, w) batch maximum
        if canvas:
            th, tw = canvas
            oh, ow = out.shape[:2]
            if oh < th or ow < tw:
                pad_v, pad_h = max(0, th - oh), max(0, tw - ow)
                white = (255, 255, 255) if out.ndim == 3 else 255
                out = cv2.copyMakeBorder(
                    out, pad_v // 2, pad_v - pad_v // 2,
                    pad_h // 2, pad_h - pad_h // 2,
                    cv2.BORDER_CONSTANT, value=white)
            detail["canvas"] = [int(out.shape[0]), int(out.shape[1])]

        changed = out.shape != image.shape or out is not image
        return QCStageResult(stage=self.name, deduction=deduction,
                             auto_fail=auto_fail, fixed=changed, flags=flags,
                             detail=detail, image=out if changed else None)
