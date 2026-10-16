"""QC Stage 5 — Advanced checks (HLD §5).

Blur (Laplacian variance), shadow removal, fold/crease detection in the
margin zone, and page size normalisation with Lanczos4. Deductions per
Appendix A: blur < warn → −20, < fail → −40 auto-FAIL, residual shadow
−10, unfixable crease −10.
"""
from __future__ import annotations

import cv2
import numpy as np

from qc.base import BaseQCStage, QCStageResult

CREASE_MIN_WIDTH_PX = 8
MARGIN_FRACTION = 0.12
SHADOW_RESIDUAL_STD = 35.0


def laplacian_variance(img: np.ndarray) -> float:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def remove_shadow(img: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    background = cv2.medianBlur(cv2.dilate(gray, np.ones((7, 7), np.uint8)), 21)
    normalised = cv2.divide(gray, background, scale=255)
    if img.ndim == 3:
        gain = cv2.divide(normalised.astype(np.float32),
                          np.maximum(gray.astype(np.float32), 1))
        return np.clip(img.astype(np.float32) * gain[..., None], 0, 255).astype(np.uint8)
    return normalised


def fix_creases(img: np.ndarray) -> tuple[np.ndarray, bool, bool]:
    """Inpaint dark linear artefacts in the margin strips.
    Returns (image, found, fixed)."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    h, w = gray.shape
    strip_w = max(1, int(w * MARGIN_FRACTION))
    mask = np.zeros((h, w), np.uint8)
    dark = (gray < 100).astype(np.uint8) * 255
    # vertical dark lines spanning most of the page height in the margins
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, h // 4))
    lines = cv2.morphologyEx(dark, cv2.MORPH_OPEN, kernel)
    mask[:, :strip_w] = lines[:, :strip_w]
    mask[:, w - strip_w:] = lines[:, w - strip_w:]
    if not mask.any():
        return img, False, False
    widths = mask.sum(axis=0) > 0
    too_wide = int(widths.sum()) > CREASE_MIN_WIDTH_PX * 3
    try:
        src = img if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        fixed = cv2.inpaint(src, cv2.dilate(mask, np.ones((3, 3), np.uint8)),
                            5, cv2.INPAINT_TELEA)
        if img.ndim == 2:
            fixed = cv2.cvtColor(fixed, cv2.COLOR_BGR2GRAY)
        return fixed, True, not too_wide
    except cv2.error:
        return img, True, False


class AdvancedStage(BaseQCStage):
    @property
    def name(self) -> str:
        return "advanced"

    def run(self, image: np.ndarray, context: dict) -> QCStageResult:
        cfg = context.get("config", {})
        warn_t = float(cfg.get("blur_warn_threshold", 80))
        fail_t = float(cfg.get("blur_fail_threshold", 30))

        deduction, flags, detail = 0, [], {}
        auto_fail = False

        blur = laplacian_variance(image)
        detail["laplacian_variance"] = round(blur, 1)
        if blur < fail_t:
            deduction += 40
            auto_fail = True
            flags.append("blur_fail")
        elif blur < warn_t:
            deduction += 20
            flags.append("blur_warn")

        # the enhance step already removed shadows and cleaned the page: measure what
        # is left, but keep the page as it is (a third whitening greys the ink)
        enhanced = bool(context.get("enhanced"))
        measured = remove_shadow(image)
        out = image if enhanced else measured
        gray = cv2.cvtColor(measured, cv2.COLOR_BGR2GRAY) if measured.ndim == 3 else measured
        bg_std = float(np.std(gray[gray > 128])) if (gray > 128).any() else 0.0
        detail["background_std"] = round(bg_std, 1)
        if bg_std > SHADOW_RESIDUAL_STD:
            deduction += 10
            flags.append("residual_shadow")

        before_creases = out
        out, crease_found, crease_fixed = fix_creases(out)
        if crease_found:
            detail["crease"] = "fixed" if crease_fixed else "unfixable"
            if not crease_fixed:
                deduction += 10
                flags.append("crease_unfixable")

        target = context.get("target_size")  # (h, w) e.g. A4 at project DPI
        if target and tuple(out.shape[:2]) != tuple(target):
            out = cv2.resize(out, (target[1], target[0]),
                             interpolation=cv2.INTER_LANCZOS4)
            detail["resized_to"] = list(target)

        changed = (not enhanced) or out is not before_creases or "resized_to" in detail
        return QCStageResult(stage=self.name, deduction=deduction,
                             auto_fail=auto_fail, fixed=changed, flags=flags,
                             detail=detail, image=out if changed else None)
