import cv2
import pytest
from PIL import Image

from export.batch_exporter import BatchExporter, ExportError, export_document
from export.filename import render
from export.pdf_writer import write_image_pdf, write_searchable_pdf
from export.tiff_writer import write_multipage_tiff
from tests.conftest import make_text_page


@pytest.fixture()
def page_files(tmp_path):
    paths = []
    for i in range(3):
        p = tmp_path / f"p{i}.png"
        cv2.imwrite(str(p), make_text_page())
        paths.append(str(p))
    return paths


# ---- filename templates --------------------------------------------------------

def test_render_all_variables():
    out = render("{date}_{barcode}_{counter:04d}_{document_type}_{batch_id}",
                 {"date": "20260714", "barcode": "B77", "counter": 7,
                  "document_type": "invoice", "batch_id": "batch1"})
    assert out == "20260714_B77_0007_invoice_batch1"


def test_render_field_variable():
    out = render("{field:invoice_no}_{counter:04d}",
                 {"fields": {"invoice_no": "INV-9"}, "counter": 12})
    assert out == "INV-9_0012"


def test_render_missing_values_and_sanitise():
    out = render("{barcode}_x<>:|?_{counter}", {"counter": 3})
    assert "<" not in out and ">" not in out and "?" not in out
    assert out.endswith("_3")


def test_render_never_empty():
    assert render("{barcode}", {}) == "document"


# ---- writers ---------------------------------------------------------------------

def test_image_pdf(page_files, tmp_path):
    out = write_image_pdf(page_files, tmp_path / "out" / "doc.pdf")
    assert out.exists() and out.stat().st_size > 1000
    assert out.read_bytes()[:5] == b"%PDF-"


def test_searchable_pdf_contains_text_layer(page_files, tmp_path):
    out = write_searchable_pdf(page_files, ["INVOICE INV-1234"] * 3,
                               tmp_path / "doc.pdf")
    data = out.read_bytes()
    assert data[:5] == b"%PDF-"
    assert data.count(b"/Type /Page") >= 3 or data.count(b"/Type/Page") >= 3


def test_multipage_tiff(page_files, tmp_path):
    out = write_multipage_tiff(page_files, tmp_path / "doc.tif")
    with Image.open(out) as im:
        assert im.n_frames == 3


def test_multipage_tiff_group4(page_files, tmp_path):
    out = write_multipage_tiff(page_files, tmp_path / "g4.tif",
                               compression="group4")
    with Image.open(out) as im:
        assert im.mode == "1" and im.n_frames == 3


# ---- export orchestration ----------------------------------------------------------

def test_export_document_uses_template_and_format(page_files, tmp_path):
    cfg = {"format": "tiff", "filename_template": "{batch_id}_{counter:04d}",
           "output_path": str(tmp_path / "exports")}
    out = export_document(page_files, cfg, {"batch_id": "b1", "counter": 2})
    assert out.name == "b1_0002.tif" and out.exists()


def test_export_document_output_folder_is_subfolder_of_output_path(
        page_files, tmp_path, monkeypatch):
    # a rule's set_folder value is relative; it must nest under output_path,
    # never resolve against the process working directory
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    cfg = {"format": "tiff", "filename_template": "{batch_id}_{counter:04d}",
           "output_path": str(tmp_path / "exports")}
    out = export_document(page_files, cfg, {"batch_id": "b1", "counter": 1,
                                            "output_folder": "Routed"})
    assert out == tmp_path / "exports" / "Routed" / "b1_0001.tif"
    assert out.exists()
    assert not (cwd / "Routed").exists()


def test_batch_exporter_retries_then_succeeds(page_files, tmp_path):
    attempts = {"n": 0}

    def flaky(images, cfg, ctx, texts):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise IOError("disk hiccup")
        return export_document(images, cfg, ctx, texts)

    exporter = BatchExporter(retry_delay=0.01)
    cfg = {"format": "image_pdf", "filename_template": "ok",
           "output_path": str(tmp_path)}
    future = exporter.submit(page_files, cfg, {}, export_fn=flaky)
    out = future.result(timeout=30)
    assert attempts["n"] == 3 and out.exists()
    exporter.shutdown()


def test_batch_exporter_flags_after_max_attempts(page_files, tmp_path):
    def always_fails(images, cfg, ctx, texts):
        raise IOError("dead disk")

    exporter = BatchExporter(retry_delay=0.01)
    future = exporter.submit(page_files, {}, {}, export_fn=always_fails)
    with pytest.raises(ExportError):
        future.result(timeout=30)
    exporter.shutdown()
