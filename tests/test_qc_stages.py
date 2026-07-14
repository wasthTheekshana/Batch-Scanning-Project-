import cv2
import numpy as np

from qc.advanced import AdvancedStage, laplacian_variance
from qc.color import ColorStage, michelson_contrast
from qc.margin import MarginStage
from qc.pipeline import run_qc
from qc.punch_hole import PunchHoleStage
from qc.rotation import RotationStage
from tests.conftest import make_text_page, rotate


def punched_page():
    page = make_text_page()
    for cy in (200, 500, 800):
        cv2.circle(page, (40, cy), 18, 30, -1)  # dark punch holes, left margin
    return page


# ---- stage 1: punch hole -------------------------------------------------------

def test_punch_holes_detected_and_inpainted():
    page = punched_page()
    result = PunchHoleStage().run(page, {"dpi": 300})
    assert result.detail.get("holes"), "expected holes to be detected"
    assert result.fixed and result.image is not None
    assert result.deduction == 0  # repaired => no deduction
    # the dark hole pixels are gone
    assert result.image[500, 40] > 128


def test_clean_page_no_holes(text_page):
    result = PunchHoleStage().run(text_page, {"dpi": 300})
    assert result.deduction == 0 and not result.detail.get("holes")


# ---- stage 2: rotation ----------------------------------------------------------

def test_rotation_fixes_180_flip(text_page):
    flipped = cv2.rotate(text_page, cv2.ROTATE_180)
    fake_osd = lambda img: {"rotate": 180, "orientation_conf": 10.0}
    result = RotationStage().run(flipped, {"osd_fn": fake_osd})
    assert result.fixed and result.deduction == 0
    assert np.array_equal(result.image, text_page) or result.image.shape == text_page.shape


def test_rotation_low_confidence_warns(text_page):
    fake_osd = lambda img: {"rotate": 0, "orientation_conf": 0.5}
    result = RotationStage().run(text_page, {"osd_fn": fake_osd})
    assert result.deduction == 5 and "low_osd_confidence" in result.flags


def test_rotation_unresolvable(text_page):
    result = RotationStage().run(text_page, {"osd_fn": lambda img: None})
    assert result.deduction == 20 and "orientation_unresolvable" in result.flags


def test_rotation_corrects_fine_skew(text_page):
    skewed = rotate(text_page, 4.0)
    fake_osd = lambda img: {"rotate": 0, "orientation_conf": 10.0}
    result = RotationStage().run(skewed, {"osd_fn": fake_osd})
    assert abs(result.detail["residual_skew"]) < 2.0
    assert "residual_skew" not in result.flags


# ---- stage 3: margin / DPI --------------------------------------------------------

def test_dpi_warn_and_fail(text_page):
    cfg = {"config": {"dpi_warn": 200, "dpi_fail": 150}}
    warn = MarginStage().run(text_page, {**cfg, "dpi": 180})
    assert warn.deduction >= 20 and not warn.auto_fail
    fail = MarginStage().run(text_page, {**cfg, "dpi": 120})
    assert fail.deduction >= 30 and fail.auto_fail


def test_margin_crops_with_safe_margin():
    canvas = np.full((1400, 1200), 255, np.uint8)
    canvas[200:1200, 200:1000] = make_text_page()
    result = MarginStage().run(canvas, {"dpi": 300, "config": {}})
    assert result.image is not None
    assert result.image.shape[0] < canvas.shape[0]


def test_margin_canvas_normalise(text_page):
    result = MarginStage().run(
        text_page, {"dpi": 300, "config": {}, "target_canvas": (1200, 1000)})
    assert result.image.shape[0] >= 1200 and result.image.shape[1] >= 1000


# ---- stage 4: colour ---------------------------------------------------------------

def test_color_stage_improves_low_contrast():
    low = make_text_page()
    low = (110 + (low.astype(np.float32) / 255) * 40).astype(np.uint8)
    before = michelson_contrast(low)
    result = ColorStage().run(low, {})
    assert michelson_contrast(result.image) > before


def test_color_stage_flags_hopeless_contrast():
    flat = np.full((500, 400), 128, np.uint8)
    result = ColorStage().run(flat, {})
    assert result.deduction == 10 and "low_contrast" in result.flags


# ---- stage 5: advanced --------------------------------------------------------------

def test_blur_warn_and_fail(text_page):
    cfg = {"config": {"blur_warn_threshold": 80, "blur_fail_threshold": 30}}
    sharp = AdvancedStage().run(text_page, cfg)
    assert "blur_warn" not in sharp.flags and "blur_fail" not in sharp.flags

    blurred = cv2.GaussianBlur(text_page, (31, 31), 12)
    assert laplacian_variance(blurred) < 30
    result = AdvancedStage().run(blurred, cfg)
    assert result.auto_fail and "blur_fail" in result.flags
    assert result.deduction >= 40


def test_size_normalise(text_page):
    result = AdvancedStage().run(
        text_page, {"config": {}, "target_size": (1100, 850)})
    assert result.image.shape[:2] == (1100, 850)


# ---- orchestrator --------------------------------------------------------------------

def _ctx():
    return {"dpi": 300, "osd_fn": lambda img: {"rotate": 0, "orientation_conf": 9.0}}


def qc_cfg(**over):
    cfg = {"enabled": True,
           "stages": ["punch_hole", "rotation", "margin", "color", "advanced"],
           "blur_warn_threshold": 80, "blur_fail_threshold": 30,
           "dpi_warn": 200, "dpi_fail": 150, "pass_score": 80}
    cfg.update(over)
    return cfg


def test_run_qc_clean_page_passes(text_page):
    fixed, verdict = run_qc(text_page, qc_cfg(), _ctx())
    assert verdict.grade == "PASS", verdict.detail
    assert fixed is not text_page  # working copy, original untouched


def test_run_qc_blurred_page_fails(text_page):
    blurred = cv2.GaussianBlur(text_page, (31, 31), 12)
    _, verdict = run_qc(blurred, qc_cfg(), _ctx())
    assert verdict.grade == "FAIL"


def test_run_qc_disabled_skips_everything(text_page):
    _, verdict = run_qc(text_page, qc_cfg(enabled=False), _ctx())
    assert verdict.score == 100 and verdict.detail.get("skipped")


def test_run_qc_stage_subset_and_skip(text_page):
    _, verdict = run_qc(text_page, qc_cfg(stages=["margin", "advanced"]), _ctx())
    assert set(verdict.detail) <= {"margin", "advanced"}
    ctx = {**_ctx(), "skip_stages": ["advanced"]}
    _, verdict = run_qc(text_page, qc_cfg(stages=["margin", "advanced"]), ctx)
    assert set(verdict.detail) == {"margin"}
