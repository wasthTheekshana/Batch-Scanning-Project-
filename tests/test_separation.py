import numpy as np

from separation.barcode import decode_barcodes
from separation.blank_split import split_on_blanks
from separation.splitter import split_documents
from separation.zone_split import check_zone_trigger
from tests.conftest import make_text_page


def page(no, blank=False, barcodes=None, image=None):
    return {"page_no": no, "image": image, "is_blank": blank,
            "barcodes": barcodes or []}


# ---- blank split -------------------------------------------------------------

def test_split_on_blanks_basic():
    docs = split_on_blanks(["a", "b", "X", "c"], [False, False, True, False])
    assert docs == [["a", "b"], ["c"]]


def test_split_on_blanks_edge_cases():
    # leading, trailing, and consecutive blanks never create empty docs
    docs = split_on_blanks(["X", "a", "X", "X", "b", "X"],
                           [True, False, True, True, False, True])
    assert docs == [["a"], ["b"]]
    assert split_on_blanks([], []) == []


# ---- orchestrator ------------------------------------------------------------

def test_blank_page_strategy():
    pages = [page(1), page(2), page(3, blank=True), page(4)]
    docs = split_documents(pages, {"strategy": ["blank_page"]})
    assert [len(d["pages"]) for d in docs] == [2, 1]
    assert docs[0]["pages"][0]["page_no"] == 1
    assert docs[1]["pages"][0]["page_no"] == 4


def test_fixed_pages_strategy():
    pages = [page(i) for i in range(1, 6)]
    docs = split_documents(pages, {"strategy": ["fixed_pages"], "fixed_pages": 2})
    assert [len(d["pages"]) for d in docs] == [2, 2, 1]


def test_barcode_strategy_starts_new_document():
    bc = [{"type": "QRCODE", "value": "DOC-2", "rect": (0, 0, 10, 10)}]
    pages = [page(1), page(2), page(3, barcodes=bc), page(4)]
    docs = split_documents(pages, {"strategy": ["barcode"]})
    assert [len(d["pages"]) for d in docs] == [2, 2]
    assert docs[1]["barcode"] == "DOC-2"
    assert docs[0]["barcode"] is None


def test_barcode_and_blank_combined():
    bc = [{"type": "CODE128", "value": "B1", "rect": (0, 0, 5, 5)}]
    pages = [page(1, barcodes=bc), page(2), page(3, blank=True),
             page(4), page(5, barcodes=bc)]
    docs = split_documents(pages, {"strategy": ["barcode", "blank_page"]})
    assert [len(d["pages"]) for d in docs] == [2, 1, 1]


def test_zone_strategy():
    img = make_text_page()  # has "INVOICE INV-1234" at top
    pages = [page(1, image=img), page(2, image=np.full((100, 100), 255, np.uint8)),
             page(3, image=img)]
    cfg = {"strategy": ["zone"],
           "zones": [{"x": 0, "y": 0, "w": 800, "h": 100, "regex": r"INVOICE"}]}
    fake_ocr = lambda region: "INVOICE INV-1234" if region.min() < 128 else ""
    docs = split_documents(pages, cfg, ocr_fn=fake_ocr)
    assert [len(d["pages"]) for d in docs] == [2, 1]


# ---- zone trigger ------------------------------------------------------------

def test_check_zone_trigger_clamps_and_matches(text_page):
    zone = {"x": 0, "y": 0, "w": 5000, "h": 5000, "regex": r"INV-\d+"}
    assert check_zone_trigger(text_page, zone, lambda r: "INV-1234")
    assert not check_zone_trigger(text_page, zone, lambda r: "nothing here")


# ---- barcode decode (graceful degradation) ------------------------------------

def test_decode_barcodes_returns_list(text_page):
    # With or without libzbar installed this must return a list, never raise.
    result = decode_barcodes(text_page)
    assert isinstance(result, list)
