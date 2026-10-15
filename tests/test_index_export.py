"""Index files (CSV / XML / JSON) written next to each exported PDF."""
import csv
import io
import json
import threading
import xml.etree.ElementTree as ET

import pytest

from export.index_export import (FIXED_COLUMNS, append_running_csv,
                                 build_record, render, write_index)


@pytest.fixture()
def pdf(tmp_path):
    path = tmp_path / "CIF-100234_0001.pdf"
    path.write_bytes(b"%PDF-1.4 fake pdf bytes")
    return path


def record(pdf, **over):
    args = dict(document_id="d-1", project_name="Bank - Customer file",
                batch_id="b-1", operator="admin", scanned_at="2026-10-15T09:30:00+00:00",
                qc_score=90, page_count=5, pdf_path=pdf,
                fields={"barcode": "CIF-100234", "doc_types": "NIC, Application",
                        "nic_number": "199012345678", "applicant_name": "Nimal Perera"})
    args.update(over)
    return build_record(**args)


# -- the record -----------------------------------------------------------------------------
def test_record_has_the_fixed_columns_first_then_the_index_fields(pdf):
    rec = record(pdf)
    assert list(rec)[:len(FIXED_COLUMNS)] == FIXED_COLUMNS
    assert list(rec)[len(FIXED_COLUMNS):] == ["nic_number", "applicant_name"]
    assert rec["barcode"] == "CIF-100234" and rec["document_types"] == "NIC, Application"
    assert rec["page_count"] == 5 and rec["pdf_file"] == "CIF-100234_0001.pdf"


def test_the_pdf_checksum_is_included(pdf):
    import hashlib
    assert record(pdf)["pdf_sha256"] == hashlib.sha256(pdf.read_bytes()).hexdigest()


def test_a_field_cannot_overwrite_a_fixed_column(pdf):
    rec = record(pdf, fields={"document_id": "evil", "pdf_sha256": "x", "ok": "1"})
    assert rec["document_id"] == "d-1" and rec["pdf_sha256"] != "x"
    assert rec["ok"] == "1"


def test_missing_values_and_a_missing_pdf_are_blank_not_errors(tmp_path):
    rec = record(tmp_path / "gone.pdf", fields={"a": None, "b": 5})
    assert rec["pdf_sha256"] == "" and rec["a"] == "" and rec["b"] == "5"


# -- CSV ------------------------------------------------------------------------------------------
def parse_csv(text):
    return list(csv.reader(io.StringIO(text)))


def test_csv_is_a_header_and_one_row(pdf):
    rows = parse_csv(render(record(pdf), "csv"))
    assert len(rows) == 2 and rows[0] == list(record(pdf))
    assert dict(zip(rows[0], rows[1]))["nic_number"] == "199012345678"


def test_csv_values_that_look_like_formulas_are_neutralised(pdf):
    rec = record(pdf, fields={"f1": "=cmd|' /C calc'!A0", "f2": "+94771234567",
                              "f3": "-1+2", "f4": "@SUM(A1)", "f5": "normal"})
    row = dict(zip(*parse_csv(render(rec, "csv"))))
    assert row["f1"].startswith("'=") and row["f4"].startswith("'@")
    assert row["f2"].startswith("'+") and row["f3"].startswith("'-")
    assert row["f5"] == "normal"


def test_csv_quotes_commas_quotes_and_newlines_and_keeps_unicode(pdf):
    rec = record(pdf, fields={"address": 'No 5, "Lake" Road\nColombo', "name": "සමන්ත பெரேரா"})
    row = dict(zip(*parse_csv(render(rec, "csv"))))
    assert row["address"] == 'No 5, "Lake" Road\nColombo'
    assert row["name"] == "සමන්ත பெரேரா"


# -- XML ---------------------------------------------------------------------------------------------
def test_xml_has_one_element_per_value_and_escapes_markup(pdf):
    rec = record(pdf, fields={"note": "<script>alert(1)</script> & more"})
    root = ET.fromstring(render(rec, "xml").encode("utf-8"))
    assert root.tag == "document"
    assert root.findtext("document_id") == "d-1"
    assert root.findtext("note") == "<script>alert(1)</script> & more"


def test_xml_field_names_that_are_not_valid_element_names_are_kept_as_attributes(pdf):
    rec = record(pdf, fields={"1st name": "A", "ok_name": "B", "a<b": "C"})
    root = ET.fromstring(render(rec, "xml").encode("utf-8"))
    bad = [e for e in root.findall("field")]
    assert {e.get("name"): e.text for e in bad} == {"1st name": "A", "a<b": "C"}
    assert root.findtext("ok_name") == "B"


def test_xml_declares_utf8(pdf):
    assert render(record(pdf), "xml").startswith('<?xml version="1.0" encoding="UTF-8"?>')


# -- JSON ------------------------------------------------------------------------------------------------
def test_json_round_trips_and_keeps_numbers_and_unicode(pdf):
    rec = record(pdf, fields={"name": "සමන්ත", "n": 7})
    back = json.loads(render(rec, "json"))
    assert back["page_count"] == 5 and back["qc_score"] == 90
    assert back["name"] == "සමන්ත" and back["n"] == "7"


def test_an_unknown_format_is_refused(pdf):
    with pytest.raises(ValueError, match="format"):
        render(record(pdf), "yaml")


# -- writing -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("fmt,ext", [("csv", ".csv"), ("xml", ".xml"), ("json", ".json")])
def test_the_file_lands_next_to_the_pdf_with_the_same_base_name(pdf, fmt, ext):
    out = write_index(pdf, record(pdf), fmt)
    assert out == pdf.with_suffix(ext) and out.exists()
    assert not list(pdf.parent.glob("*.tmp"))               # written atomically


def test_writing_again_replaces_the_file_with_the_corrected_values(pdf):
    write_index(pdf, record(pdf), "csv")
    write_index(pdf, record(pdf, fields={"nic_number": "856789012V"}), "csv")
    row = dict(zip(*parse_csv(pdf.with_suffix(".csv").read_text(encoding="utf-8-sig"))))
    assert row["nic_number"] == "856789012V"


def test_the_csv_file_opens_in_excel_with_unicode(pdf):
    out = write_index(pdf, record(pdf, fields={"name": "සමන්ත"}), "csv")
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")        # UTF-8 BOM
    assert "සමන්ත" in out.read_text(encoding="utf-8-sig")


# -- the running index.csv ---------------------------------------------------------------------------------------
def test_the_running_csv_gets_one_row_per_document(tmp_path, pdf):
    path = append_running_csv(tmp_path, record(pdf))
    append_running_csv(tmp_path, record(pdf, document_id="d-2"))
    rows = parse_csv(path.read_text(encoding="utf-8-sig"))
    assert path.name == "index.csv" and len(rows) == 3
    assert [r[0] for r in rows[1:]] == ["d-1", "d-2"]


def test_a_new_field_widens_the_running_csv_without_losing_rows(tmp_path, pdf):
    append_running_csv(tmp_path, record(pdf, fields={"a": "1"}))
    append_running_csv(tmp_path, record(pdf, document_id="d-2", fields={"a": "2", "b": "x"}))
    rows = parse_csv((tmp_path / "index.csv").read_text(encoding="utf-8-sig"))
    header = rows[0]
    assert header[-2:] == ["a", "b"] and len(rows) == 3
    first, second = (dict(zip(header, r)) for r in rows[1:])
    assert first["a"] == "1" and first["b"] == "" and second["b"] == "x"


def test_a_document_reissued_replaces_its_row_in_the_running_csv(tmp_path, pdf):
    append_running_csv(tmp_path, record(pdf, fields={"a": "old"}))
    append_running_csv(tmp_path, record(pdf, fields={"a": "corrected"}))
    rows = parse_csv((tmp_path / "index.csv").read_text(encoding="utf-8-sig"))
    assert len(rows) == 2 and dict(zip(rows[0], rows[1]))["a"] == "corrected"


def test_concurrent_documents_all_reach_the_running_csv(tmp_path, pdf):
    def add(n):
        append_running_csv(tmp_path, record(pdf, document_id=f"d-{n}"))
    threads = [threading.Thread(target=add, args=(n,)) for n in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = parse_csv((tmp_path / "index.csv").read_text(encoding="utf-8-sig"))
    assert sorted(r[0] for r in rows[1:]) == sorted(f"d-{n}" for n in range(20))
