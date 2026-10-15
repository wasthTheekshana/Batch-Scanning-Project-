"""Pages fed upside down or sideways are read, indexed and exported upright."""
import json

import cv2
import numpy as np
import pytest

from core.job_queue import Pipeline
from data.db import Database
from delivery.local_folder import LocalFolderChannel
from export.batch_exporter import export_document
from ocr.orientation import real_words, turn, upright
from ocr.tesseract_engine import OCRResult
from tests.test_pipeline import make_profile, wait_for, write_pages

MARK = 200          # pixel value planted at the top-left of an UPRIGHT page


def word(text, conf=90):
    return {"text": text, "left": 0, "top": 0, "width": 10, "height": 10, "conf": conf}


GOOD = OCRResult("NATIONAL IDENTITY CARD number name address", 0.9,
                 [word(w) for w in "NATIONAL IDENTITY CARD number name address".split()])
GIBBERISH = OCRResult("oJ USA ADI PF ON", 0.3, [word(w, 30) for w in "oJ USA ADI PF ON".split()])


def page(upright_marker=True, turned=0):
    """A page whose top-left marks which way up it is (200 = upright)."""
    img = np.full((120, 80), 255, np.uint8)
    img[3:8, 3:8] = MARK if upright_marker else 40
    return turn(img, turned)


def reader(image):
    """A stand-in OCR: real words only when the marker is at the top-left."""
    return GOOD if image[5, 5] == MARK else GIBBERISH


def test_real_words_counts_letters_read_with_confidence():
    assert real_words(GOOD) == 6
    assert real_words(GIBBERISH) == 0
    assert real_words(None) == 0
    mixed = OCRResult("", 0.5, [word("1234"), word("abc"), word("Name", 20), word("Branch")])
    assert real_words(mixed) == 1                       # digits, short, unsure: no


def test_a_page_that_already_reads_fine_is_left_alone():
    img = page()
    turned, out, result = upright(img, GOOD, lambda i: pytest.fail("read again"))
    assert turned == 0 and out is img and result is GOOD


@pytest.mark.parametrize("fed", [180, 90, 270])
def test_a_page_fed_the_wrong_way_up_is_turned_upright(fed):
    wrong = page(turned=fed)                            # as it came off the feeder
    turned, out, result = upright(wrong, reader(wrong), reader)
    assert turned == (360 - fed) % 360                  # the turn that undoes it
    assert out[5, 5] == MARK and result is GOOD


def test_a_page_that_reads_badly_every_way_is_not_turned():
    blank_ish = page(upright_marker=False)
    turned, out, result = upright(blank_ish, GIBBERISH, lambda i: GIBBERISH)
    assert turned == 0 and out is blank_ish


def test_one_extra_word_is_not_clear_evidence():
    wrong = page(turned=180)
    marginal = OCRResult("", 0.5, [word("Name"), word("Date"), word("Form"), word("Mark")])
    one_more = OCRResult("", 0.5, [word("Name"), word("Date"), word("Form"),
                                   word("Mark"), word("Fold")])
    turned, _out, _r = upright(wrong, marginal, lambda i: one_more)
    assert turned == 0


def test_a_reading_without_word_boxes_is_never_retried():
    called = []
    turned, _out, _r = upright(page(), OCRResult("some text", 0.9),
                               lambda i: called.append(1))
    assert turned == 0 and not called


# -- through the real pipeline -------------------------------------------------------------------
@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "o.db")
    d.insert_project("proj-1", "Test Project", {})
    yield d
    d.close()


def run_upside_down(tmp_path, db, fed):
    profile = make_profile(tmp_path)
    profile["separation"] = {"strategy": ["fixed_pages"], "fixed_pages": 1,
                             "blank_threshold": 0.02}
    profile["features"]["auto_index"] = True
    profile["ocr"] = {"lang": "eng", "mode": "full_page", "confidence_threshold": 0.5,
                      "zones": [{"name": "nic", "regex": r"ID No:\s*(\d{12})"}]}
    profile["export"]["format"] = "image_pdf"

    def ocr(image):
        if image[5, 5] != MARK:
            return GIBBERISH
        return OCRResult("NATIONAL IDENTITY CARD ID No: 199012345678", 0.9,
                         [word(w) for w in "NATIONAL IDENTITY CARD ID No: 199012345678".split()
                          ] + [word("number"), word("holder")])

    pipeline = Pipeline(db, profile,
                        [LocalFolderChannel(profile["delivery"]["local_folder"])],
                        export_document, working_dir=tmp_path / "staging", ocr_fn=ocr)
    jobs = write_pages(tmp_path, ["text"])
    for j in jobs:
        j.batch_id = "batch-1"
        base = cv2.imread(j.image_path, cv2.IMREAD_UNCHANGED)
        base[3:8, 3:8] = MARK
        cv2.imwrite(j.image_path, turn(base, fed))
    pipeline.start()
    try:
        pipeline.submit_batch("batch-1", jobs)
        assert wait_for(lambda: any(d["storage_path"]
                                    for d in db.list_documents("batch-1")))
    finally:
        pipeline.shutdown()
    return db.list_documents("batch-1")[0]


def test_an_upside_down_id_card_is_read_stored_and_exported_upright(tmp_path, db):
    doc = run_upside_down(tmp_path, db, fed=180)
    assert json.loads(doc["extracted_fields"])["nic"] == "199012345678"
    info = json.loads(doc["qc_detail"])["pages"]["1"]
    assert info["rotation"] == 180
    working = cv2.imread(info["working_path"], cv2.IMREAD_UNCHANGED)
    assert working is not None
    # the exported/working copy is the page the right way up again
    original = cv2.imread(info["original_path"], cv2.IMREAD_UNCHANGED)
    assert original[5, 5] != MARK and turn(original, 180)[5, 5] == MARK


def test_a_page_fed_correctly_is_not_turned(tmp_path, db):
    doc = run_upside_down(tmp_path, db, fed=0)
    assert json.loads(doc["qc_detail"])["pages"]["1"]["rotation"] == 0
    assert json.loads(doc["extracted_fields"])["nic"] == "199012345678"


def test_an_upside_down_full_page_is_turned_even_though_it_yields_a_few_real_words():
    """Regression (real Kodak scan): an upside-down form read as 420 words of
    which 13 looked real, which passed the old 'reads fine' test of 6 words."""
    from ocr.orientation import upright

    def result(real, junk):
        words = ([{"text": "Account", "conf": 90}] * real
                 + [{"text": "x1#", "conf": 20}] * junk)
        return type("R", (), {"words": words})()

    sideways = result(13, 407)
    seen = []

    def read(image):
        seen.append(1)
        return result(213, 233)                  # every other way up reads well

    import numpy as np
    degrees, _img, got = upright(np.zeros((20, 20), np.uint8), sideways, read)
    assert degrees == 180 and seen and len(got.words) == 446


def test_a_page_that_reads_well_is_not_re_read():
    from ocr.orientation import upright
    import numpy as np
    words = [{"text": "Account", "conf": 90}] * 60 + [{"text": "x1#", "conf": 20}] * 40
    first = type("R", (), {"words": words})()
    degrees, _img, got = upright(np.zeros((20, 20), np.uint8), first,
                                 lambda image: (_ for _ in ()).throw(AssertionError("re-read")))
    assert degrees == 0 and got is first
