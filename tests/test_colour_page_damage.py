"""Dark filled areas (section bars, logos) and round margin content must survive enhancement and QC."""
import cv2
import numpy as np

from imaging.enhance import whiten_background
from qc.punch_hole import PunchHoleStage


def _page_with_bar():
    rng = np.random.default_rng(1)
    img = np.full((600, 800, 3), 235, np.uint8)
    img[100:260, 100:700] = 60                                   # dark section bar
    img[100:260, 100:700] += rng.integers(0, 25, (160, 600, 1), dtype=np.uint8)   # halftone texture
    cv2.putText(img, "Personal", (130, 200), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 4)
    return img


def test_a_dark_bar_stays_dark_and_the_paper_is_still_whitened():
    img = _page_with_bar()
    out = cv2.cvtColor(whiten_background(img), cv2.COLOR_BGR2GRAY)
    bar = out[110:250, 110:700]
    bar = bar[bar < 200]
    assert np.mean(out[110:250, 110:700] < 120) > 0.6            # still mostly dark, not a pale patch
    assert out[400:550, 100:700].mean() > 235                    # paper stays white


def test_round_content_in_the_margin_is_not_mistaken_for_a_punch_hole():
    img = np.full((800, 600, 3), 240, np.uint8)
    cv2.circle(img, (40, 400), 20, (0, 0, 0), -1)                 # a real hole
    cv2.putText(img, "O", (30, 150), cv2.FONT_HERSHEY_SIMPLEX, 3, (0, 0, 0), 6)   # a big round letter
    img[600:700, 0:70] = 50                                       # dark logo block touching the margin
    result = PunchHoleStage().run(img, {"dpi": 300})
    out = result.image if result.image is not None else img
    assert out[600:700, 0:70].mean() < 80                         # the dark block is untouched
    assert out[130:170, 30:70].min() < 50                          # the letter is untouched
