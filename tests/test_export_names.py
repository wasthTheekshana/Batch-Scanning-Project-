"""Two documents must never end up in the same file. Every form of a bundle carries the same barcode and the
counter restarts at 1 in every batch, so "{barcode}_{counter:04d}" named different documents alike and the second
batch replaced the first batch's PDFs."""
import cv2
import numpy as np
import pytest

from export.batch_exporter import export_document


@pytest.fixture()
def page(tmp_path):
    path = tmp_path / "page.png"
    cv2.imwrite(str(path), np.full((300, 200, 3), 255, np.uint8))
    return str(path)


def export(tmp_path, page, **ctx):
    cfg = {"format": "image_pdf", "output_path": str(tmp_path / "out"), "filename_template": "{barcode}_{counter:04d}"}
    return export_document([page], cfg, {"batch_id": "b", "counter": 1, "barcode": "DOK-BUNDLE-X", **ctx})


def test_a_second_document_with_the_same_name_does_not_replace_the_first(tmp_path, page):
    first = export(tmp_path, page, document_id="aaaaaaaa-1111")
    second = export(tmp_path, page, document_id="bbbbbbbb-2222")       # next batch: same barcode, counter 1 again
    assert first.name == "DOK-BUNDLE-X_0001.pdf"
    assert second != first and second.name == "DOK-BUNDLE-X_0001_bbbbbbbb.pdf"
    assert first.exists() and second.exists()


def test_the_same_document_exported_again_lands_on_the_same_name(tmp_path, page):
    export(tmp_path, page, document_id="aaaaaaaa-1111")
    again_1 = export(tmp_path, page, document_id="bbbbbbbb-2222")
    again_2 = export(tmp_path, page, document_id="bbbbbbbb-2222")
    assert again_1 == again_2
    assert len(list((tmp_path / "out").glob("*.pdf"))) == 2


def test_a_document_without_a_barcode_is_named_for_it(tmp_path, page):
    path = export(tmp_path, page, barcode="", document_id="cccccccc-3333")
    assert path.name == "NO-BARCODE_0001.pdf"


def test_without_a_document_id_a_taken_name_still_gets_a_free_one(tmp_path, page):
    first = export(tmp_path, page)
    second = export(tmp_path, page)
    assert first != second and first.exists() and second.exists()
