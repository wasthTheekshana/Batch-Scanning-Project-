import cv2
import numpy as np
import pytest

from ocr.duplicate import find_duplicate, fingerprint, phash_distance
from ocr.tesseract_engine import OCRResult, is_available, ocr_page
from ocr.zone_extractor import extract_fields
from tests.conftest import make_text_page

tesseract_missing = not is_available()


# ---- tesseract engine ---------------------------------------------------------

@pytest.mark.skipif(tesseract_missing, reason="tesseract binary not on PATH")
def test_ocr_page_reads_rendered_text():
    img = np.full((200, 900), 255, np.uint8)
    cv2.putText(img, "HELLO DOK 12345", (30, 120),
                cv2.FONT_HERSHEY_SIMPLEX, 2.0, 0, 5)
    result = ocr_page(img)
    assert "12345" in result.text.replace(" ", "")
    assert 0.0 < result.confidence <= 1.0


# ---- zone extraction -----------------------------------------------------------

def test_extract_fields_with_regex_cleanup(text_page):
    fake = lambda region: OCRResult(text="Invoice No: INV-1234 ", confidence=0.9)
    zones = [{"name": "invoice_no", "x": 0, "y": 0, "w": 800, "h": 100,
              "regex": r"(INV-\d+)"}]
    fields = extract_fields(text_page, zones, ocr_fn=fake)
    assert fields["invoice_no"].value == "INV-1234"
    assert fields["invoice_no"].confidence == 0.9


def test_extract_fields_no_match_yields_empty(text_page):
    fake = lambda region: OCRResult(text="nothing useful", confidence=0.5)
    zones = [{"name": "invoice_no", "x": 0, "y": 0, "w": 100, "h": 50,
              "regex": r"INV-\d+"}]
    assert extract_fields(text_page, zones, ocr_fn=fake)["invoice_no"].value == ""


def test_extract_fields_without_regex_keeps_text(text_page):
    fake = lambda region: OCRResult(text="  ACME Corp  ", confidence=0.8)
    zones = [{"name": "vendor", "x": 0, "y": 0, "w": 100, "h": 50}]
    assert extract_fields(text_page, zones, ocr_fn=fake)["vendor"].value == "ACME Corp"


def test_extract_fields_skips_zone_missing_geometry():
    """A template-seeded zone (name+regex, no x/y/w/h yet) must be
    silently skipped, not crash extraction."""
    from ocr.zone_extractor import extract_fields
    import numpy as np
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    zones = [{"name": "unpositioned", "regex": r"(\S+)"},
             {"name": "positioned", "x": 0, "y": 0, "w": 50, "h": 50}]

    def fake_ocr(region):
        from ocr.tesseract_engine import OCRResult
        return OCRResult(text="VALUE", confidence=0.9)

    fields = extract_fields(img, zones, ocr_fn=fake_ocr)
    assert "unpositioned" not in fields
    assert fields["positioned"].value == "VALUE"


def test_extract_fields_from_text_matches_label_anchor():
    from ocr.zone_extractor import extract_fields_from_text
    text = "Some header text\nInvoice No: INV-7777\nVendor: ACME Corp\nfooter"
    zones = [{"name": "invoice_no", "regex": r"Invoice No:\s*(\S+)"},
             {"name": "vendor", "regex": r"Vendor:\s*(.+)"}]
    fields = extract_fields_from_text(text, 0.87, zones)
    assert fields["invoice_no"].value == "INV-7777"
    assert fields["invoice_no"].confidence == 0.87
    assert fields["vendor"].value == "ACME Corp"
    assert fields["vendor"].confidence == 0.87


def test_extract_fields_from_text_no_match_yields_zero_confidence():
    from ocr.zone_extractor import extract_fields_from_text
    zones = [{"name": "invoice_no", "regex": r"Invoice No:\s*(\S+)"}]
    fields = extract_fields_from_text("nothing relevant here", 0.9, zones)
    assert fields["invoice_no"].value == ""
    assert fields["invoice_no"].confidence == 0.0


def test_extract_fields_from_text_skips_zone_without_regex():
    from ocr.zone_extractor import extract_fields_from_text
    zones = [{"name": "unanchored"},
             {"name": "invoice_no", "regex": r"Invoice No:\s*(\S+)"}]
    fields = extract_fields_from_text("Invoice No: INV-1", 0.9, zones)
    assert "unanchored" not in fields
    assert fields["invoice_no"].value == "INV-1"


# ---- duplicate detection --------------------------------------------------------

def test_exact_duplicate_by_md5(tmp_path):
    img = make_text_page()
    p1, p2 = tmp_path / "a.png", tmp_path / "b.png"
    cv2.imwrite(str(p1), img)
    cv2.imwrite(str(p2), img)
    md5a, ph_a = fingerprint(p1)
    md5b, ph_b = fingerprint(p2)
    assert md5a == md5b
    dup = find_duplicate(md5b, ph_b,
                         [{"document_id": "d1", "md5": md5a, "phash": ph_a}])
    assert dup == "d1"


def test_near_duplicate_by_phash(tmp_path):
    img = make_text_page()
    noisy = img.copy()
    noise = np.random.default_rng(42).integers(0, 12, img.shape, np.uint8)
    noisy = cv2.subtract(noisy, noise)
    p1, p2 = tmp_path / "a.png", tmp_path / "b.png"
    cv2.imwrite(str(p1), img)
    cv2.imwrite(str(p2), noisy)
    md5a, ph_a = fingerprint(p1)
    md5b, ph_b = fingerprint(p2)
    assert md5a != md5b
    assert phash_distance(ph_a, ph_b) <= 5
    dup = find_duplicate(md5b, ph_b,
                         [{"document_id": "d1", "md5": md5a, "phash": ph_a}])
    assert dup == "d1"


def test_barcode_cross_check():
    dup = find_duplicate("md5x", "0" * 16,
                         [{"document_id": "d9", "md5": "other",
                           "phash": "f" * 16, "barcode": "B-77"}],
                         barcode="B-77")
    assert dup == "d9"


def test_no_duplicate_returns_none():
    assert find_duplicate("m", "0" * 16, []) is None
