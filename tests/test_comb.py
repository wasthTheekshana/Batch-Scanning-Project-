"""Boxed-character (comb) fields: [1][9][9][0]... one character per cell."""
import numpy as np
import pytest

from ocr.comb import CHARSETS, find_cells, read_comb
from ocr.zone_extractor import extract_fields

# a colour a little lighter than the typed text, like a printed form's boxes
BORDER, HINT, INK = 105, 170, 10
CELL, GAP = 44, 0


def comb_strip(text, cells=None, hints="", border=BORDER, slash_at=None):
    """A strip like the forms': boxes with pale hint letters, text typed in."""
    import cv2
    cells = cells or len(text)
    width, height = cells * CELL + 8, 60
    strip = np.full((height, width), 255, np.uint8)
    for i in range(cells + 1):
        x = 4 + i * CELL
        cv2.line(strip, (x, 6), (x, height - 7), border, 2)
    cv2.line(strip, (4, 6), (width - 5, 6), border, 2)
    cv2.line(strip, (4, height - 7), (width - 5, height - 7), border, 2)
    for i, ch in enumerate(hints):
        cv2.putText(strip, ch, (4 + i * CELL + 12, 42), cv2.FONT_HERSHEY_SIMPLEX,
                    0.9, HINT, 2, cv2.LINE_AA)
    for i, ch in enumerate(text):
        if ch != " ":
            cv2.putText(strip, ch, (4 + i * CELL + 11, 42), cv2.FONT_HERSHEY_SIMPLEX,
                        1.0, INK, 2, cv2.LINE_AA)
    return strip


def test_the_cells_between_the_dividers_are_found():
    cells = find_cells(255 - comb_strip("1234567890") if False else
                       (comb_strip("1234567890") < 150).astype(np.uint8) * 255)
    assert len(cells) == 10
    assert all(abs((b - a) - CELL) <= 2 for a, b in cells)


def test_a_typed_account_number_is_read_exactly():
    assert read_comb(comb_strip("0012345678"), "digits").text == "0012345678"


def test_letters_and_digits_are_read_in_an_alphanumeric_box():
    assert read_comb(comb_strip("N1234567"), "alnum").text == "N1234567"


def test_a_nic_number_with_a_trailing_letter():
    assert read_comb(comb_strip("856789012V"), "nic").text == "856789012V"


def test_empty_boxes_are_skipped_not_read_as_noise():
    assert read_comb(comb_strip("12 45", cells=5), "digits").text == "1245"


def test_pale_hint_letters_printed_in_the_boxes_are_not_part_of_the_entry():
    strip = comb_strip("12011990", hints="DDMMYYYY")
    assert read_comb(strip, "digits").text == "12011990"


def test_hint_letters_alone_read_as_an_empty_field():
    assert read_comb(comb_strip("", cells=8, hints="DDMMYYYY"), "digits").text == ""


def test_a_strip_that_is_not_a_row_of_boxes_reads_as_empty():
    assert read_comb(np.full((40, 300), 255, np.uint8), "digits").text == ""


def test_confidence_reflects_how_sure_the_readings_were():
    result = read_comb(comb_strip("0012345678"), "digits")
    assert 0.5 < result.confidence <= 1.0


def test_the_charsets_are_what_the_zone_dialog_offers():
    assert set(CHARSETS) == {"digits", "letters", "alnum", "nic"}


def test_unrecognised_charset_names_are_used_as_the_literal_allowed_characters():
    assert read_comb(comb_strip("1010"), "01").text == "1010"


# -- through the zone extractor ---------------------------------------------------------
def test_extract_fields_reads_a_comb_zone_from_a_page():
    page = np.full((300, 700), 255, np.uint8)
    strip = comb_strip("199012345678")
    page[100:100 + strip.shape[0], 20:20 + strip.shape[1]] = strip
    zones = [{"name": "nic_number", "comb": True, "charset": "nic",
              "x": 10, "y": 90, "w": strip.shape[1] + 20, "h": strip.shape[0] + 20,
              "regex": r"(\d{9}[VvXx]|\d{12})"}]
    got = extract_fields(page, zones)
    assert got["nic_number"].value == "199012345678"


def test_the_regex_cleans_the_comb_reading_like_any_other_zone():
    page = np.full((200, 600), 255, np.uint8)
    strip = comb_strip("123456")
    page[50:50 + strip.shape[0], 10:10 + strip.shape[1]] = strip
    zones = [{"name": "n", "comb": True, "charset": "digits", "regex": r"(\d{4})",
              "x": 0, "y": 40, "w": 590, "h": 90}]
    assert extract_fields(page, zones)["n"].value == "1234"
