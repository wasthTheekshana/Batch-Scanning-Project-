"""Regression (found by the OCR benchmark): a thin grey box around a short word
was not erased, so the reader returned nothing for 'Matara' at 200 dpi."""
import cv2
import numpy as np

from ocr.zone_extractor import prepare_strip


def boxed_word(text="Matara", line=105, height=39, width=428):
    img = np.full((height, width), 255, np.uint8)
    cv2.rectangle(img, (4, 3), (width - 5, height - 5), line, 1)
    cv2.putText(img, text, (14, height - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.9, 0, 2)
    return img


def longest_grey_row(img) -> int:
    return int(((img > 60) & (img < 200)).sum(axis=1).max())


def test_the_thin_grey_horizontal_box_lines_are_erased():
    assert longest_grey_row(boxed_word()) > 300                  # the box is there
    assert longest_grey_row(prepare_strip(boxed_word())) < 150   # and gone


def test_a_short_word_in_a_thin_box_is_read():
    from ocr.tesseract_engine import ocr_page
    text = ocr_page(prepare_strip(boxed_word()), psm=7, min_conf=0).text
    assert text.strip(" |") == "Matara"          # a stray edge mark is stripped by the field regex


def test_dark_ink_crossing_the_box_is_kept():
    plain = boxed_word()
    crossed = boxed_word()
    cv2.line(crossed, (60, 1), (60, 8), 0, 2)           # a stroke through the top edge
    extra = (prepare_strip(crossed) < 40).sum() - (prepare_strip(plain) < 40).sum()
    assert extra > 10
