"""PDF export: lossless image-only PDF via img2pdf and searchable PDF
with an invisible OCR text layer via reportlab (HLD §13)."""
from __future__ import annotations

import textwrap
from pathlib import Path

import img2pdf
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas as rl_canvas


def write_image_pdf(images: list[str | Path], out: str | Path) -> Path:
    """Lossless embedding; preserves exact scan resolution; fastest."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f:
        f.write(img2pdf.convert([str(p) for p in images]))
    return out


FONT = "Helvetica"
_LINE_CHARS = 120          # fallback layer: characters per text line
_MIN_WORD_PT = 2.0


def _latin(text: str) -> str:
    """The PDF's standard font holds Western characters only; anything else
    (Sinhala, Tamil) is dropped rather than turned into boxes or an error."""
    return text.encode("cp1252", errors="ignore").decode("cp1252").strip()


def _draw_words(c, words: list[dict], h_pt: float, dpi: int) -> None:
    """Invisible text, one word at a time, stretched to the word's own box so
    selecting or searching in a PDF viewer lands on the printed word."""
    scale = 72.0 / dpi
    t = c.beginText()
    t.setTextRenderMode(3)                      # invisible
    for w in words:
        text = _latin(str(w.get("text", "")))
        width = float(w.get("width", 0)) * scale
        height = float(w.get("height", 0)) * scale
        if not text or width < _MIN_WORD_PT or height < _MIN_WORD_PT:
            continue
        size = max(height, _MIN_WORD_PT)
        natural = pdfmetrics.stringWidth(text, FONT, size)
        if natural <= 0:
            continue
        t.setFont(FONT, size)
        t.setHorizScale(100.0 * width / natural)
        left = float(w.get("left", 0)) * scale
        top = float(w.get("top", 0)) * scale
        # baseline sits about a fifth of the box height above its bottom edge
        t.setTextOrigin(left, h_pt - top - height + 0.2 * height)
        t.textOut(text)
    c.drawText(t)


def _draw_lines(c, text: str, h_pt: float) -> None:
    """No word boxes known: keep all the text, wrapped at word boundaries and
    sized so every line stays on the page."""
    lines: list[str] = []
    for raw in text.splitlines() or [text]:
        lines += textwrap.wrap(_latin(raw), _LINE_CHARS,
                               break_long_words=True) or []
    if not lines:
        return
    leading = max(2.0, min(10.0, (h_pt - 24.0) / len(lines)))
    t = c.beginText(0, h_pt - leading - 2)
    t.setTextRenderMode(3)
    t.setFont(FONT, leading)
    t.setLeading(leading)
    for line in lines:
        t.textLine(line)
    c.drawText(t)


def write_searchable_pdf(images: list[str | Path],
                         ocr_texts: list[str],
                         out: str | Path,
                         dpi: int = 300,
                         words: list[list[dict]] | None = None) -> Path:
    """Page image with an invisible text layer so the PDF is full-text
    searchable. `words` (one list per page, boxes in image pixels as
    ocr.tesseract_engine reports them) places every word where it is printed;
    a page without boxes falls back to its plain text from `ocr_texts`
    (aligned with images; an empty string adds no text)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    c = rl_canvas.Canvas(str(out))
    for i, (img_path, text) in enumerate(zip(images, ocr_texts)):
        with Image.open(img_path) as im:
            w_pt = im.width * 72.0 / dpi
            h_pt = im.height * 72.0 / dpi
            c.setPageSize((w_pt, h_pt))
            c.drawImage(ImageReader(im), 0, 0, width=w_pt, height=h_pt)
        page_words = words[i] if words and i < len(words) else None
        if page_words:
            _draw_words(c, page_words, h_pt, dpi)
        elif text:
            _draw_lines(c, text, h_pt)
        c.showPage()
    c.save()
    return out
