"""Lining zones up with a scan of the form (ocr.align) and the digit model."""
import cv2
import numpy as np
import pytest

from ocr.align import build_anchors, dewarp, estimate_transform

ANCHORS = [{"t": "account", "x": 40, "y": 100}, {"t": "branch", "x": 40, "y": 130},
           {"t": "mobile", "x": 40, "y": 400}, {"t": "passport", "x": 40, "y": 430},
           {"t": "nationality", "x": 40, "y": 300}, {"t": "address", "x": 40, "y": 340},
           {"t": "civil", "x": 40, "y": 360}]


def scan_words(matrix, anchors=ANCHORS, extra=()):
    """OCR words as they would be found after the page went through `matrix`."""
    words = []
    for a in list(anchors) + list(extra):
        x, y = matrix @ np.array([a["x"], a["y"], 1.0])
        words.append({"text": a["t"].capitalize(), "left": x - 20, "top": y - 8,
                      "width": 40, "height": 16})
    return words


def similarity(scale, angle_deg, tx, ty):
    a = np.deg2rad(angle_deg)
    return np.array([[scale * np.cos(a), -scale * np.sin(a), tx],
                     [scale * np.sin(a), scale * np.cos(a), ty]])


def test_the_scale_shift_and_rotation_of_a_scan_are_recovered():
    truth = similarity(4.05, 0.8, 102, 111)            # a ~97% print of an A4 form
    found = estimate_transform(ANCHORS, scan_words(truth))
    assert found is not None
    assert np.allclose(found, truth, atol=0.05, rtol=0.01)


def test_dewarping_puts_template_positions_back_where_they_were_drawn():
    """A mark placed at template (30, 40) on a scan that was shrunk, shifted
    and rotated lands at (30, 40) x density after dewarping."""
    truth = similarity(4.0, 1.5, 120, 80)
    page = np.full((900, 700), 255, np.uint8)
    cx, cy = (truth @ np.array([30.0, 40.0, 1.0])).round().astype(int)
    cv2.circle(page, (cx, cy), 6, 0, -1)
    straight = dewarp(page, truth, ref_w=100, ref_h=120)
    assert straight.shape == (480, 400)                  # 100 x 120 units at 4 px
    ys, xs = np.nonzero(straight < 128)
    assert abs(xs.mean() - 30 * 4.0) < 2 and abs(ys.mean() - 40 * 4.0) < 2


def test_repeated_labels_in_a_second_column_do_not_throw_the_fit_off():
    truth = similarity(4.0, 0, 100, 50)
    right_column = [{"t": a["t"], "x": a["x"] + 280, "y": a["y"]} for a in ANCHORS]
    doubled_template = ANCHORS + right_column            # every label twice
    found = estimate_transform(doubled_template, scan_words(truth, doubled_template))
    assert found is not None and np.allclose(found, truth, atol=0.1)


def test_words_that_were_misread_are_outliers_not_a_wrong_answer():
    truth = similarity(4.0, 0, 100, 50)
    words = scan_words(truth)
    words[0]["left"] += 400                              # one label found far away
    found = estimate_transform(ANCHORS, words)
    assert found is not None and np.allclose(found, truth, atol=0.1)


def test_too_few_anchor_words_means_no_alignment():
    truth = similarity(4.0, 0, 100, 50)
    assert estimate_transform(ANCHORS, scan_words(truth, ANCHORS[:3])) is None
    assert estimate_transform(ANCHORS, []) is None
    assert estimate_transform([], scan_words(truth)) is None


def test_an_implausible_fit_is_refused():
    absurd = similarity(0.01, 0, 5, 5)
    assert estimate_transform(ANCHORS, scan_words(absurd)) is None


def test_anchors_are_the_words_that_are_distinctive_on_the_blank_form():
    words = [{"text": "Account", "x": 1, "y": 2}, {"text": "Branch", "x": 3, "y": 4},
             {"text": "Branch", "x": 9, "y": 4}, {"text": "of", "x": 5, "y": 6},
             {"text": "Name:", "x": 7, "y": 8}, {"text": "Name:", "x": 8, "y": 8},
             {"text": "Name:", "x": 9, "y": 8}, {"text": "1234", "x": 1, "y": 1}]
    anchors = build_anchors(words)
    texts = [a["t"] for a in anchors]
    assert texts.count("branch") == 2 and "account" in texts      # twice is fine
    assert "name" not in texts and "of" not in texts and "1234" not in texts


# -- the digit model --------------------------------------------------------------------------
def glyph(char, size=60, thickness=2):
    img = np.zeros((size + 20, size + 20), np.uint8)
    cv2.putText(img, char, (12, size + 4), cv2.FONT_HERSHEY_SIMPLEX, 2.0, 255, thickness)
    return img


def test_the_digit_model_reads_handwritten_samples():
    """Real 8x8 handwritten samples (the format the network was trained on)."""
    import json
    from pathlib import Path
    from ocr import digits
    samples = json.loads((Path(__file__).parent / "fixtures" / "digits_8x8.json")
                         .read_text(encoding="utf-8"))
    assert digits.available()
    results = [(digit, digits.predict(np.array(sample, np.float32) / 16.0))
               for digit, group in samples.items() for sample in group]
    correct = sum(1 for digit, (read, _p) in results if read == digit)
    assert correct >= 28, f"{correct}/30"
    assert all(0.0 < p <= 1.0 for _d, (_r, p) in results)


def test_a_glyph_becomes_the_networks_eight_by_eight_form():
    from ocr import digits
    features = digits.glyph_to_features(glyph("3"))
    assert features.shape == (64,) and 0.0 <= features.min() and features.max() <= 1.0
    assert features.sum() > 4                      # ink was found
    assert digits.glyph_to_features(np.zeros((40, 40), np.uint8)) is None


def test_heavy_pen_strokes_are_thinned_to_the_training_weight():
    from ocr import digits
    heavy = digits.glyph_to_features(glyph("1", thickness=14))
    light = digits.glyph_to_features(glyph("1", thickness=3))
    assert abs(float(heavy.sum()) - float(light.sum())) < 0.45 * float(light.sum())


def test_an_empty_cell_has_no_digit():
    from ocr import digits
    assert digits.read_digit(np.zeros((50, 40), np.uint8)) == ("", 0.0)


def test_without_the_model_file_digits_are_simply_not_read(monkeypatch, tmp_path):
    from ocr import digits
    monkeypatch.setattr(digits, "MODEL_PATH", tmp_path / "missing.npz")
    digits._model.cache_clear()
    try:
        assert digits.read_digit(glyph("5")) == ("", 0.0)
    finally:
        digits._model.cache_clear()


def test_a_digit_box_trusts_the_engine_when_it_is_sure_and_the_model_when_it_is_not(monkeypatch):
    from ocr import comb
    cell = glyph("7")
    monkeypatch.setattr(comb, "_tesseract_char", lambda c, w: ("7", 0.9))
    assert comb._read_char(cell, comb.CHARSETS["digits"])[0] == "7"      # engine, sure
    monkeypatch.setattr(comb, "_tesseract_char", lambda c, w: ("2", 0.3))
    model_says = comb._read_char(glyph("4"), comb.CHARSETS["digits"])[0]
    assert model_says == "4"                                            # engine unsure
    monkeypatch.setattr(comb, "_tesseract_char", lambda c, w: ("V", 0.8))
    assert comb._read_char(cell, comb.CHARSETS["nic"])[0] == "V"        # NIC letter kept


def test_letter_boxes_never_go_through_the_digit_model(monkeypatch):
    from ocr import comb
    monkeypatch.setattr(comb, "_tesseract_char", lambda c, w: ("N", 0.3))
    assert comb._read_char(glyph("1"), comb.CHARSETS["alnum"]) == ("N", 0.3)


def test_leftover_box_lines_are_stripped_from_a_cell_but_the_digit_stays():
    from ocr import comb
    cell = glyph("7")
    cell[0:5, :] = 255                       # a thick bar from the box's top border
    cell[-3:-1, ::3] = 255                   # a dotted bottom line
    cleaned = comb._clean_cell(cell)
    assert cleaned[0:5, :].sum() == 0
    assert np.count_nonzero(cleaned) > 0.5 * np.count_nonzero(glyph("7"))
