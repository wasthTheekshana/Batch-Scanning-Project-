"""A QR code the first decoder cannot read is tried with a second one; and a form's title page starts a document
even when its QR code could not be read at all (the scanner's threshold had fattened the modules)."""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from separation import barcode
from separation.splitter import split_documents

zxingcpp = pytest.importorskip("zxingcpp")
ROOT = Path(__file__).parent.parent


def qr_page(value="DOK-BUNDLE-385130378D6D"):
    import qrcode
    code = np.array(qrcode.make(value).convert("L"))
    page = np.full((900, 700), 255, np.uint8)
    page[40:40 + code.shape[0], 300:300 + code.shape[1]] = code
    return page


def test_the_second_decoder_reads_what_the_first_cannot(monkeypatch):
    monkeypatch.setattr(barcode, "_load_pyzbar", lambda: SimpleNamespace(decode=lambda view: []))
    found = barcode.decode_barcodes(qr_page())
    assert [b["value"] for b in found] == ["DOK-BUNDLE-385130378D6D"] and found[0]["type"] == "QRCODE"
    assert all(len(b["rect"]) == 4 for b in found)


def test_a_page_with_no_barcode_at_all_still_finds_none(monkeypatch):
    monkeypatch.setattr(barcode, "_load_pyzbar", lambda: SimpleNamespace(decode=lambda view: []))
    assert barcode.decode_barcodes(np.full((300, 300), 255, np.uint8)) == []


def test_a_decoder_that_is_missing_never_breaks_separation(monkeypatch):
    monkeypatch.setattr(barcode, "_load_pyzbar", lambda: None)
    monkeypatch.setitem(__import__("sys").modules, "zxingcpp", None)           # "import zxingcpp" now fails
    assert barcode.decode_barcodes(qr_page()) == []


def _template(name):
    return json.loads((ROOT / "core" / "templates" / name).read_text(encoding="utf-8"))["config"]


@pytest.mark.parametrize("name", ["bank_customer_file.json", "bank_demo_index.json"])
def test_the_bank_templates_cut_at_each_forms_title_page_as_well_as_at_barcodes(name):
    cfg = _template(name)
    assert cfg["separation"]["strategy"] == ["barcode", "document_type"]
    forms = [t for t in cfg["ocr"]["document_types"] if t.get("starts_document")]
    assert {t["name"] for t in forms} == {"Credit card application", "Home loan application",
                                         "Education loan application", "Personal loan application"}


def test_a_form_whose_qr_code_could_not_be_read_still_starts_its_own_document():
    cfg = _template("bank_demo_index.json")["separation"]
    qr = [{"type": "QRCODE", "value": "DOK-BUNDLE-X", "rect": (0, 0, 9, 9)}]
    page = lambda n, qr_, title: {"page_no": n, "image": None, "is_blank": False, "ocr_text": "x" * 80,   # noqa: E731
                                  "barcodes": qr_, "starts_document": title}
    pages = [page(1, qr, True), page(2, [], False),                  # form 1: QR read
             page(3, [], True), page(4, [], False),                  # form 2: QR spoiled, title page found
             page(5, qr, True), page(6, [], False)]                  # form 3
    docs = split_documents(pages, cfg)
    assert [[p["page_no"] for p in d["pages"]] for d in docs] == [[1, 2], [3, 4], [5, 6]]
    only_qr = {**cfg, "strategy": ["barcode"]}
    assert len(split_documents(pages, only_qr)) == 2                  # what happened before: form 2 glued to form 1
