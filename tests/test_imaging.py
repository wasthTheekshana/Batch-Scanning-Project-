import numpy as np

from imaging.blank_detect import ink_density, is_blank
from imaging.crop import auto_crop, content_bbox
from imaging.deskew import deskew, detect_skew_angle
from imaging.enhance import apply_clahe, apply_profile, to_color_mode
from tests.conftest import make_text_page, rotate


# ---- deskew -----------------------------------------------------------------

def test_detects_and_corrects_skew(text_page):
    skewed = rotate(text_page, 4.0)
    detected = detect_skew_angle(skewed)
    assert 2.0 < abs(detected) < 6.0
    fixed, applied = deskew(skewed)
    assert abs(detect_skew_angle(fixed)) < 1.0
    assert applied != 0.0


def test_straight_page_untouched(text_page):
    fixed, applied = deskew(text_page)
    assert applied == 0.0
    assert fixed is text_page


# ---- crop -------------------------------------------------------------------

def test_auto_crop_preserves_content(text_page):
    # embed the page in a larger canvas with big white borders
    canvas = np.full((1400, 1200), 255, np.uint8)
    canvas[200:1200, 200:1000] = text_page
    cropped = auto_crop(canvas, margin_px=40)
    assert cropped.shape[0] < canvas.shape[0]
    assert cropped.shape[1] < canvas.shape[1]
    # content plus margin retained: ink density must survive the crop
    assert ink_density(cropped) >= ink_density(canvas)


def test_crop_blank_returns_original(blank_page):
    assert auto_crop(blank_page).shape == blank_page.shape


def test_content_bbox_none_for_blank(blank_page):
    assert content_bbox(blank_page) is None


# ---- blank detection ---------------------------------------------------------

def test_blank_detection(blank_page, text_page):
    assert is_blank(blank_page)
    assert not is_blank(text_page)


# ---- enhancement -------------------------------------------------------------

def test_apply_profile_respects_toggles(text_page):
    off = {"whiten_bg": False, "clahe": False, "despeckle": False}
    assert np.array_equal(apply_profile(text_page, off), text_page)
    on = {"whiten_bg": True, "clahe": True, "despeckle": True}
    out = apply_profile(text_page, on)
    assert out.shape == text_page.shape


def test_clahe_improves_contrast():
    low = make_text_page()
    # squash contrast: map [0,255] -> [110,150]
    low = (110 + (low.astype(np.float32) / 255) * 40).astype(np.uint8)
    out = apply_clahe(low)
    assert int(out.max()) - int(out.min()) > int(low.max()) - int(low.min())


def test_color_mode_conversion(text_page):
    color = to_color_mode(text_page, "color")
    assert color.ndim == 3
    gray = to_color_mode(color, "grayscale")
    assert gray.ndim == 2
    bw = to_color_mode(color, "bw")
    assert set(np.unique(bw)) <= {0, 255}
