import fitz
import numpy as np
import pytest

from scanning.cover_sheets import (CoverSheetError, parse_ids, sequence_ids,
                                   write_cover_sheets)
from separation.barcode import decode_barcodes


def render(pdf_path, page_no, dpi=200):
    """The page as a scanner would deliver it (grayscale pixels)."""
    with fitz.open(str(pdf_path)) as pdf:
        pix = pdf[page_no].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
        return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)


def values(page_pixels):
    return sorted(b["value"] for b in decode_barcodes(page_pixels))


# -- parsing ---------------------------------------------------------------------
def test_parse_ids_splits_lines_commas_and_keeps_order_without_duplicates():
    ids, rejected = parse_ids("CIF-1\n CIF-2 ,CIF-3;CIF-1\r\n\n  \nCIF-4")
    assert ids == ["CIF-1", "CIF-2", "CIF-3", "CIF-4"]
    assert rejected == []


def test_parse_ids_rejects_unprintable_or_overlong_entries():
    ids, rejected = parse_ids("OK-1\nNimal Perera é\n" + "X" * 41 + "\nOK-2")
    assert ids == ["OK-1", "OK-2"]
    assert len(rejected) == 2


def test_sequence_ids_pads_and_counts():
    assert sequence_ids("CIF-", 100234, 3) == ["CIF-100234", "CIF-100235",
                                               "CIF-100236"]
    assert sequence_ids("A", 7, 2, width=4) == ["A0007", "A0008"]


@pytest.mark.parametrize("start,count", [(1, 0), (-1, 5), (1, 5001)])
def test_sequence_ids_refuses_nonsense(start, count):
    with pytest.raises(CoverSheetError):
        sequence_ids("A", start, count)


# -- the printed barcodes can be read back by the scanner's own decoder --------------
def test_each_cover_sheet_page_decodes_to_its_id(tmp_path):
    out = write_cover_sheets(["CIF-100234", "CIF-100235", "NIC-199012345678"],
                             tmp_path / "covers.pdf")
    with fitz.open(str(out)) as pdf:
        assert pdf.page_count == 3
    assert [values(render(out, n)) for n in range(3)] == [
        ["CIF-100234"], ["CIF-100235"], ["NIC-199012345678"]]


def test_a_label_sheet_holds_many_barcodes_that_all_decode(tmp_path):
    ids = [f"CIF-{n}" for n in range(100234, 100240)]
    out = write_cover_sheets(ids, tmp_path / "labels.pdf", layout="labels")
    assert values(render(out, 0)) == sorted(ids)


def test_copies_repeat_each_label_in_order(tmp_path):
    out = write_cover_sheets(["A-1", "B-2"], tmp_path / "l.pdf",
                             layout="labels", copies=3)
    with fitz.open(str(out)) as pdf:
        printed = pdf[0].get_text().split()
    assert printed == ["A-1"] * 3 + ["B-2"] * 3
    assert values(render(out, 0)) == ["A-1", "B-2"]   # the decoder merges repeats


def test_more_labels_than_one_sheet_hold_flow_onto_further_pages(tmp_path):
    ids = [f"ID-{n:03d}" for n in range(35)]            # 30 per sheet
    out = write_cover_sheets(ids, tmp_path / "many.pdf", layout="labels")
    with fitz.open(str(out)) as pdf:
        assert pdf.page_count == 2
    assert len(values(render(out, 0))) == 30
    assert len(values(render(out, 1))) == 5


def test_a_cover_sheet_survives_being_fed_upside_down(tmp_path):
    out = write_cover_sheets(["CIF-100234"], tmp_path / "c.pdf")
    assert values(np.rot90(render(out, 0), 2).copy()) == ["CIF-100234"]


# -- errors -------------------------------------------------------------------------------
def test_nothing_to_print_is_an_error(tmp_path):
    with pytest.raises(CoverSheetError, match="no IDs"):
        write_cover_sheets([], tmp_path / "x.pdf")


def test_an_id_too_long_for_a_label_is_refused_not_printed_unreadably(tmp_path):
    with pytest.raises(CoverSheetError, match="too long"):
        write_cover_sheets(["X" * 40], tmp_path / "x.pdf", layout="labels")


def test_unknown_layout_is_refused(tmp_path):
    with pytest.raises(CoverSheetError, match="layout"):
        write_cover_sheets(["A"], tmp_path / "x.pdf", layout="poster")
