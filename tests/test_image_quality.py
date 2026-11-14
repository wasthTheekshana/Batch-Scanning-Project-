"""Enhancement must make a scan clearer or leave it alone, never blur it.

Found on real Kodak scans (scripts/quality_loop.py): the old chain halved the
sharpness of grayscale text and made black-and-white pages unreadable, because a
3x3 median blur smoothed every stroke and the same filters then ran again in QC.
"""
import cv2
import numpy as np
import pytest

from imaging.deskew import MIN_CORRECTION_DEG, deskew
from imaging.enhance import (CLAHE_BELOW, apply_profile, binarize, contrast,
                             despeckle, to_color_mode)
from qc.advanced import AdvancedStage
from qc.color import ColorStage
from qc.rotation import RotationStage
from tests.conftest import make_text_page, rotate

ON = {"whiten_bg": True, "clahe": True, "despeckle": True}


def sharpness(img):
    return float(cv2.Laplacian(img, cv2.CV_64F).var())


def real_scan_page(seed=1, scale=0.5):
    """Text like a real scan: thin antialiased 1-pixel strokes, a little optical blur
    and scanner noise. (make_text_page has strokes too thick to show the old bug.)"""
    rng = np.random.default_rng(seed)
    page = np.full((1100, 800), 245, np.uint8)
    words = "The benefit shall be payable subject to the conditions of this policy".split()
    y = 60
    for line in range(36):
        text = " ".join(words[(line + k) % len(words)] for k in range(8))
        cv2.putText(page, text, (40, y), cv2.FONT_HERSHEY_TRIPLEX, scale, 25, 1, cv2.LINE_AA)
        y += 28
    page = cv2.GaussianBlur(page, (0, 0), 0.7)
    return np.clip(page + rng.normal(0, 3, page.shape), 0, 255).astype(np.uint8)


def old_despeckle(img):
    """What the application used to do (a 3x3 median blur, then an opening)."""
    return cv2.morphologyEx(cv2.medianBlur(img, 3), cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2)))


def test_this_fixture_would_have_caught_the_original_bug():
    """The fixture is only worth having if the old filter fails it, as it did on real scans."""
    page = real_scan_page()
    old = old_despeckle(page)
    assert sharpness(old) < 0.8 * sharpness(page)                    # the old filter blurred it
    assert np.percentile(old, 2) > np.percentile(page, 2) + 10       # ... and greyed the ink


# -- the filters keep strokes sharp ----------------------------------------------------------
@pytest.mark.parametrize("scale", [0.45, 0.5, 0.6])
def test_the_default_filters_do_not_blur_text(scale):
    page = real_scan_page(scale=scale)
    out = apply_profile(page, ON)
    assert sharpness(out) >= 0.95 * sharpness(page)


def test_the_text_stays_as_black_as_it_was():
    page = real_scan_page()
    out = apply_profile(page, ON)
    assert np.percentile(out, 2) <= np.percentile(page, 2) + 5          # the old chain greyed it


def test_despeckle_removes_dust_and_nothing_else():
    page = make_text_page()
    rng = np.random.default_rng(3)
    # dust in the margins: single dark pixels and 2-pixel specks
    dust = []
    for _ in range(40):
        y, x = int(rng.integers(5, 28)), int(rng.integers(5, page.shape[1] - 5))   # the margin: text starts at row 36
        page[y, x] = 0
        dust.append((y, x))
    for y, x in [(10, 100), (12, 300)]:
        page[y:y + 1, x:x + 2] = 0
    before = (page < 128).sum()
    out = despeckle(page)
    assert all(out[y, x] > 200 for y, x in dust)                       # the dust is gone
    changed = out != page
    assert int(changed.sum()) <= 40 + 4                                # ... and only the dust changed
    assert (out < 128).sum() >= before - 44


def test_full_stops_and_the_dots_of_an_i_are_kept():
    page = np.full((200, 300), 255, np.uint8)
    cv2.circle(page, (50, 50), 2, 0, -1)                               # 5x5: a full stop at 300 dpi
    page[100:103, 100:103] = 0                                         # 3x3: the dot of an i
    page[150:151, 200:202] = 0                                         # 1x2: dust
    out = despeckle(page, dpi=300)
    assert out[50, 50] == 0 and out[101, 101] == 0                     # kept
    assert out[150, 200] == 255                                        # removed


def test_the_speck_size_scales_with_the_resolution():
    page = np.full((100, 100), 255, np.uint8)
    page[50:53, 50:53] = 0                                             # 9 px
    assert despeckle(page, dpi=300)[51, 51] == 0                       # a mark at 300 dpi
    assert despeckle(page, dpi=1200)[51, 51] == 255                    # dust at 1200 dpi (16x the area)


def test_a_page_with_no_dust_comes_back_untouched():
    page = make_text_page()
    assert despeckle(page) is page or np.array_equal(despeckle(page), page)


# -- local contrast only where the page is flat -------------------------------------------------
def test_a_page_with_good_contrast_is_not_contrast_enhanced():
    page = make_text_page()
    assert contrast(page) >= CLAHE_BELOW
    out = apply_profile(page, {"clahe": True})
    assert np.array_equal(out, page)


def test_a_faded_page_is_contrast_enhanced():
    faded = (110 + (make_text_page().astype(np.float32) / 255) * 40).astype(np.uint8)
    assert contrast(faded) < CLAHE_BELOW
    out = apply_profile(faded, {"clahe": True})
    assert int(out.max()) - int(out.min()) > int(faded.max()) - int(faded.min())


# -- deskew only when it pays for the resampling ------------------------------------------------------
def test_a_tiny_tilt_is_left_alone_and_a_real_one_is_corrected():
    page = make_text_page()
    tilt = 0.2
    assert tilt < MIN_CORRECTION_DEG
    slightly = rotate(page, tilt)
    fixed, applied = deskew(slightly)
    assert applied == 0.0 and fixed is slightly                        # not resampled
    fixed, applied = deskew(rotate(page, 3.0))
    assert applied != 0.0


# -- QC measures an already-enhanced page and does not filter it again --------------------------------------
def test_the_colour_stage_does_not_re_filter_an_enhanced_page():
    page = apply_profile(make_text_page(), ON)
    result = ColorStage().run(page, {"enhanced": True})
    assert result.image is None and not result.fixed
    assert "michelson_contrast" in result.detail                        # still measured


def test_the_colour_stage_still_fixes_a_page_nobody_enhanced():
    faded = (110 + (make_text_page().astype(np.float32) / 255) * 40).astype(np.uint8)
    result = ColorStage().run(faded, {})
    assert result.image is not None and result.fixed


def test_the_advanced_stage_keeps_an_enhanced_page_as_it_is():
    page = apply_profile(make_text_page(), ON)
    result = AdvancedStage().run(page, {"config": {}, "enhanced": True})
    assert result.image is None and "laplacian_variance" in result.detail
    resized = AdvancedStage().run(page, {"config": {}, "enhanced": True, "target_size": (1100, 850)})
    assert resized.image is not None and resized.image.shape[:2] == (1100, 850)


def test_the_rotation_stage_measures_but_does_not_resample_an_enhanced_page():
    page = make_text_page()
    skewed = rotate(page, 2.0)
    osd = lambda img: {"rotate": 0, "orientation_conf": 9.0}
    again = RotationStage().run(skewed, {"osd_fn": osd, "enhanced": True})
    assert again.detail["fine_skew_applied"] == 0.0 and again.image is None
    first = RotationStage().run(skewed, {"osd_fn": osd})
    assert first.detail["fine_skew_applied"] != 0.0 and first.image is not None


# -- black and white, decided in software --------------------------------------------------------------
def test_binarize_gives_pure_black_and_white():
    out = binarize(make_text_page())
    assert set(np.unique(out)) <= {0, 255}
    assert to_color_mode(make_text_page(), "bw").dtype == np.uint8


def test_faint_thin_text_survives_where_a_fixed_threshold_loses_it():
    """Pale print (grey 150 on paper 235) under a shadow gradient."""
    page = np.full((300, 600), 235, np.uint8)
    gradient = np.linspace(0, 60, 600, dtype=np.float32)[None, :]
    page = np.clip(page - gradient, 0, 255).astype(np.uint8)            # shadow towards the right
    for y in range(40, 260, 30):
        cv2.putText(page, "Faint account number 12345", (20, y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, int(150 - 0.0), 1, cv2.LINE_AA)
    fixed = np.where(page > 128, 255, 0)                                # a scanner's own 1-bit mode
    adaptive = binarize(page, dpi=200)
    assert (fixed == 0).sum() < 50                                      # the fixed threshold lost the text
    assert (adaptive == 0).sum() > 500                                  # the adaptive one kept it
    shadow_only = adaptive[:, 560:]                                     # ... without turning shadow black
    assert (shadow_only == 0).mean() < 0.2


def test_a_solid_black_area_stays_black():
    page = np.full((200, 300), 240, np.uint8)
    page[50:150, 50:250] = 10
    out = binarize(page)
    assert (out[60:140, 60:240] == 0).all() and (out[:30] == 255).all()


# -- through the real pipeline and the Scan wizard ------------------------------------------------------------------
def test_a_black_and_white_project_delivers_a_black_and_white_working_copy(tmp_path):
    import json

    from pipeline.job_queue import Pipeline
    from data.db import Database
    from delivery.local_folder import LocalFolderChannel
    from export.batch_exporter import export_document
    from tests.test_pipeline import make_profile, wait_for, write_pages
    working_pixels = {}
    for mode in ("bw", "grayscale"):
        folder = tmp_path / mode
        folder.mkdir()
        db = Database(folder / "q.db")
        db.insert_project("proj-1", "P", {})
        profile = make_profile(folder)
        profile["scanner"]["color"] = mode
        profile["separation"] = {"strategy": ["fixed_pages"], "fixed_pages": 1, "blank_threshold": 0.02}
        pipe = Pipeline(db, profile, [LocalFolderChannel(profile["delivery"]["local_folder"])],
                        export_document, working_dir=folder / "staging")
        jobs = write_pages(folder, ["text"])
        for j in jobs:
            j.batch_id = "b1"
        pipe.start()
        try:
            pipe.submit_batch("b1", jobs, operator="admin")
            assert wait_for(lambda: db.list_documents("b1") and db.list_documents("b1")[0]["qc_detail"])
        finally:
            pipe.shutdown()
        detail = json.loads(db.list_documents("b1")[0]["qc_detail"])
        working = cv2.imread(detail["pages"]["1"]["working_path"], cv2.IMREAD_UNCHANGED)
        working_pixels[mode] = set(np.unique(working).tolist())
        db.close()
    assert working_pixels["bw"] <= {0, 255}
    assert len(working_pixels["grayscale"]) > 2                          # grayscale keeps its grey levels


def test_a_black_and_white_project_is_scanned_in_grayscale(qtbot):
    from ui.scan_wizard import ScanWizard
    projects = [{"project_id": "p1", "name": "BW", "config": {"scanner": {"dpi": 300, "color": "bw", "duplex": True}}},
                {"project_id": "p2", "name": "Gray", "config": {"scanner": {"dpi": 200, "color": "grayscale"}}},
                {"project_id": "p3", "name": "Colour", "config": {"scanner": {"color": "color"}}}]
    wizard = ScanWizard(projects, None, devices={})
    qtbot.addWidget(wizard)
    colours = {}
    for i, p in enumerate(projects):
        wizard.project_page.combo.setCurrentIndex(i)
        colours[p["name"]] = wizard._scanner_settings(projects)["color"]
    assert colours == {"BW": "grayscale", "Gray": "grayscale", "Colour": "color"}


def test_the_whole_chain_keeps_text_as_sharp_as_the_scan():
    """Deskew, crop, enhance and then every QC stage, as the application runs them."""
    from imaging.crop import auto_crop
    from qc.pipeline import run_qc
    page = real_scan_page()
    profile = {"deskew": True, "crop": True, "clahe": True, "despeckle": True,
               "whiten_bg": True, "target_dpi": 300}
    out, _ = deskew(page)
    out = apply_profile(auto_crop(out), profile)
    qc = {"enabled": True, "stages": ["punch_hole", "rotation", "margin", "color", "advanced"],
          "pass_score": 85, "blur_warn_threshold": 80, "blur_fail_threshold": 30}
    fixed, _verdict = run_qc(out, qc, {"dpi": 300, "enhanced": True,
                                       "osd_fn": lambda img: {"rotate": 0, "orientation_conf": 9.0}})
    assert sharpness(fixed) >= 0.9 * sharpness(page)
    assert np.percentile(fixed, 2) <= np.percentile(page, 2) + 8
