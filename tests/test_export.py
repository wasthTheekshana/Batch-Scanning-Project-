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


@pytest.mark.parametrize("bad", ["../evil", "D:/evil", "\\\\h\\s", "C:evil",
                                 "a/../../evil"])
def test_export_document_rejects_escaping_output_folder(page_files, tmp_path, bad):
    root = tmp_path / "root"
    cfg = {"format": "tiff", "filename_template": "{batch_id}_{counter:04d}",
           "output_path": str(root / "exports")}
    before = {p for p in tmp_path.rglob("*")}
    with pytest.raises(ValueError, match="unsafe output folder"):
        export_document(page_files, cfg, {"batch_id": "b1", "counter": 1,
                                          "output_folder": bad})
    assert {p for p in tmp_path.rglob("*")} == before  # nothing written anywhere


def test_export_document_rejects_a_reserved_device_file_name(page_files, tmp_path):
    cfg = {"format": "image_pdf", "filename_template": "CON",
           "output_path": str(tmp_path / "out")}
    before = {p for p in tmp_path.rglob("*")}
    with pytest.raises(ValueError, match="unsafe output file name"):
        export_document(page_files, cfg, {"batch_id": "b1", "counter": 1})
    assert {p for p in tmp_path.rglob("*")} == before


def test_export_document_verifies_the_composed_path_stays_in_the_output_dir(
        page_files, tmp_path, monkeypatch):
    # defence in depth: even if the renderer ever let a separator through
    monkeypatch.setattr("export.batch_exporter.render",
                        lambda template, ctx: "../escaped")
    cfg = {"format": "image_pdf", "filename_template": "x",
           "output_path": str(tmp_path / "out")}
    before = {p for p in tmp_path.rglob("*")}
    with pytest.raises(ValueError, match="unsafe output file name"):
        export_document(page_files, cfg, {})
    assert {p for p in tmp_path.rglob("*")} == before
    assert not (tmp_path / "escaped.pdf").exists()


def test_export_document_still_writes_a_normal_name_inside_the_output_dir(
        page_files, tmp_path):
    cfg = {"format": "image_pdf", "filename_template": "report..final",
           "output_path": str(tmp_path / "out")}
    out = export_document(page_files, cfg, {})
    assert out.parent == tmp_path / "out" and out.exists()


# ---- searchable PDF: the text layer must sit on the words ----------------------

def _word_rects(pdf_path, page=0):
    import fitz
    with fitz.open(str(pdf_path)) as pdf:
        return [(w[4], fitz.Rect(w[:4])) for w in pdf[page].get_text("words")]


def test_text_layer_is_placed_on_each_word(tmp_path):
    img = tmp_path / "p.png"
    cv2.imwrite(str(img), make_text_page())              # 800 x 1000 px at 300 dpi
    words = [{"text": "INVOICE", "left": 100, "top": 40, "width": 300, "height": 40},
             {"text": "INV-1234", "left": 420, "top": 40, "width": 340, "height": 40},
             {"text": "Total", "left": 100, "top": 500, "width": 150, "height": 36}]
    out = write_searchable_pdf([img], ["INVOICE INV-1234 Total"], tmp_path / "d.pdf",
                               dpi=300, words=[words])
    found = dict(_word_rects(out))
    assert set(found) == {"INVOICE", "INV-1234", "Total"}
    scale = 72 / 300
    for w in words:
        r = found[w["text"]]
        assert r.x0 == pytest.approx(w["left"] * scale, abs=3)
        assert r.x1 == pytest.approx((w["left"] + w["width"]) * scale, abs=4)
        assert r.y0 == pytest.approx(w["top"] * scale, abs=4)
        assert r.y1 == pytest.approx((w["top"] + w["height"]) * scale, abs=5)


def test_text_layer_without_boxes_keeps_all_text(tmp_path):
    img = tmp_path / "p.png"
    cv2.imwrite(str(img), make_text_page())
    text = " ".join(f"word{i}" for i in range(1200))       # 8,000+ characters
    out = write_searchable_pdf([img], [text], tmp_path / "d.pdf")
    import fitz
    with fitz.open(str(out)) as pdf:
        got = pdf[0].get_text()
    assert "word0" in got and "word1199" in got


def test_text_the_pdf_font_cannot_hold_does_not_break_export(tmp_path):
    img = tmp_path / "p.png"
    cv2.imwrite(str(img), make_text_page())
    words = [{"text": "සිංහල", "left": 100, "top": 40, "width": 200, "height": 40},
             {"text": "Name", "left": 320, "top": 40, "width": 150, "height": 40}]
    out = write_searchable_pdf([img], ["x"], tmp_path / "d.pdf", words=[words])
    assert "Name" in dict(_word_rects(out))


def test_a_page_with_no_words_still_gets_its_image(tmp_path):
    img = tmp_path / "p.png"
    cv2.imwrite(str(img), make_text_page())
    out = write_searchable_pdf([img, img], ["", "second"], tmp_path / "d.pdf",
                               words=[[], []])
    import fitz
    with fitz.open(str(out)) as pdf:
        assert pdf.page_count == 2 and pdf[0].get_images()


@pytest.mark.skipif(not __import__("ocr.tesseract_engine", fromlist=["x"]).is_available(),
                    reason="Tesseract not installed")
def test_real_ocr_words_are_found_where_they_are_printed(tmp_path):
    from ocr.tesseract_engine import ocr_page
    page = make_text_page()
    cv2.putText(page, "Customer Account", (100, 900), cv2.FONT_HERSHEY_SIMPLEX,
                1.6, 0, 3)
    img = tmp_path / "p.png"
    cv2.imwrite(str(img), page)
    result = ocr_page(page)
    out = write_searchable_pdf([img], [result.text], tmp_path / "d.pdf",
                               dpi=300, words=[result.words])
    import fitz
    with fitz.open(str(out)) as pdf:
        hits = pdf[0].search_for("Account")
    assert hits, "the printed word cannot be found in the PDF text layer"
    r = hits[0]
    scale = 72 / 300
    assert 850 * scale < r.y1 < 960 * scale          # the baseline area it was printed on
    assert r.x0 > 100 * scale
