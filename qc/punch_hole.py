"""QC Stage 1 — Punch hole removal (HLD §5).

HoughCircles on the left+right 12% margin strips; detected holes are
inpainted with TELEA (white-fill fallback on bright uniform paper).
Deduction: −10 only when holes are found but cannot be repaired.
"""
from __future__ import annotations

import cv2
import numpy as np

from qc.base import BaseQCStage, QCStageResult

MARGIN_FRACTION = 0.12
MIN_RADIUS = 8    # at 300 DPI
MAX_RADIUS = 40
WHITE_FILL_BRIGHTNESS = 240


def _is_hole(strip: np.ndarray, cx: int, cy: int, r: float) -> bool:
    """A real hole is a solid dark disc inside a ring of bright paper. Round-ish
    letters, logos and halftone bars also fool the circle detector, and painting
    over them destroys content, so check what the circle actually contains."""
    h, w = strip.shape
    yy, xx = np.ogrid[:h, :w]
    dist = np.hypot(xx - cx, yy - cy)
    inside = strip[dist <= r * 0.8]
    ring = strip[(dist >= r + 3) & (dist <= r * 1.5 + 4)]
    if inside.size == 0 or ring.size < 20:
        return False
    return (float(np.mean(inside < 128)) >= 0.9          # solid dark
            and float(np.mean(ring > 180)) >= 0.85)      # paper all round it


class PunchHoleStage(BaseQCStage):
    @property
    def name(self) -> str:
        return "punch_hole"

    def run(self, image: np.ndarray, context: dict) -> QCStageResult:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        h, w = gray.shape
        strip_w = max(1, int(w * MARGIN_FRACTION))
        scale = context.get("dpi", 300) / 300.0
        rmin = max(3, int(MIN_RADIUS * scale))
        rmax = max(rmin + 1, int(MAX_RADIUS * scale))

        holes: list[tuple[int, int, int]] = []
        for x_off, strip in ((0, gray[:, :strip_w]), (w - strip_w, gray[:, w - strip_w:])):
            blurred = cv2.medianBlur(strip, 5)
            circles = cv2.HoughCircles(
                blurred, cv2.HOUGH_GRADIENT, dp=1, minDist=rmax * 2,
                param1=80, param2=15, minRadius=rmin, maxRadius=rmax)
            if circles is not None:
                for cx, cy, r in np.asarray(circles).reshape(-1, 3):
                    # punch holes scan dark: require a dark centre
                    cyi, cxi = int(cy), int(cx)
                    if 0 <= cyi < strip.shape[0] and 0 <= cxi < strip.shape[1] \
                            and strip[cyi, cxi] < 128 and _is_hole(strip, cxi, cyi, float(r)):
                        holes.append((int(cx) + x_off, int(cy), int(r)))

        if not holes:
            return QCStageResult(stage=self.name)

        mask = np.zeros((h, w), np.uint8)
        for cx, cy, r in holes:
            cv2.circle(mask, (cx, cy), r + 4, 255, -1)

        bg_brightness = float(np.mean(gray[mask == 0]))
        try:
            src = image if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
            fixed = cv2.inpaint(src, mask, 5, cv2.INPAINT_TELEA)
            if image.ndim == 2:
                fixed = cv2.cvtColor(fixed, cv2.COLOR_BGR2GRAY)
            if bg_brightness > WHITE_FILL_BRIGHTNESS:
                white = (255, 255, 255) if fixed.ndim == 3 else 255
                for cx, cy, r in holes:
                    cv2.circle(fixed, (cx, cy), r + 2, white, -1)
            return QCStageResult(
                stage=self.name, deduction=0, fixed=True, image=fixed,
                detail={"holes": holes, "method": "telea+whitefill"
                        if bg_brightness > WHITE_FILL_BRIGHTNESS else "telea"})
        except cv2.error as e:
            return QCStageResult(
                stage=self.name, deduction=10, fixed=False,
                flags=["punch_holes_uncorrectable"],
                detail={"holes": holes, "error": str(e)})
