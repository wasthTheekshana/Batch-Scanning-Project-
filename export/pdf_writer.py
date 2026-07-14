"""PDF export: lossless image-only PDF via img2pdf and searchable PDF
with an invisible OCR text layer via reportlab (HLD §13)."""
from __future__ import annotations

from pathlib import Path

import img2pdf
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas


def write_image_pdf(images: list[str | Path], out: str | Path) -> Path:
    """Lossless embedding; preserves exact scan resolution; fastest."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f:
        f.write(img2pdf.convert([str(p) for p in images]))
    return out


def write_searchable_pdf(images: list[str | Path],
                         ocr_texts: list[str],
                         out: str | Path,
                         dpi: int = 300) -> Path:
    """Page image with the OCR text drawn in invisible render mode so the
    PDF is full-text searchable. ocr_texts aligns with images (empty
    string = no text layer for that page)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    c = rl_canvas.Canvas(str(out))
    for img_path, text in zip(images, ocr_texts):
        with Image.open(img_path) as im:
            w_pt = im.width * 72.0 / dpi
            h_pt = im.height * 72.0 / dpi
            c.setPageSize((w_pt, h_pt))
            c.drawImage(ImageReader(im), 0, 0, width=w_pt, height=h_pt)
        if text:
            t = c.beginText(0, h_pt - 12)
            t.setTextRenderMode(3)  # invisible
            t.setFont("Helvetica", 10)
            for line in text.splitlines() or [text]:
                t.textLine(line[:1000])
            c.drawText(t)
        c.showPage()
    c.save()
    return out
