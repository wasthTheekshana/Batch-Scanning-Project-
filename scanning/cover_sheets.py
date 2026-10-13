"""Printable barcode cover sheets and sticker labels for customer files.

A cover sheet (one page per ID) goes on top of a customer's papers; the
labels (a grid of small barcodes) are stuck on the first page of a document.
Both carry a Code128 barcode of the ID and the ID in readable letters, and
the barcode value becomes the document's barcode field when scanned.
"""
from __future__ import annotations

import re
from pathlib import Path

from reportlab.graphics.barcode import code128
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

MAX_ID_LENGTH = 40
MAX_IDS = 5000
MAX_COPIES = 50

_PRINTABLE = re.compile(r"^[\x20-\x7e]+$")

# sticker sheet: 3 columns x 10 rows on A4 (70 x 28 mm cells)
LABEL_COLUMNS, LABEL_ROWS = 3, 10
MIN_BAR_WIDTH = 0.7          # points; thinner bars do not decode reliably at 200 dpi


class CoverSheetError(ValueError):
    """The ID list cannot be printed (message is safe to show)."""


def parse_ids(text: str) -> tuple[list[str], list[str]]:
    """Split pasted text into IDs (one per line, or comma/semicolon
    separated). Returns (unique valid IDs in order, rejected entries)."""
    ids: list[str] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[\r\n,;]+", text or ""):
        value = raw.strip()
        if not value:
            continue
        if len(value) > MAX_ID_LENGTH or not _PRINTABLE.match(value):
            rejected.append(value[:MAX_ID_LENGTH + 3])
            continue
        if value not in seen:
            seen.add(value)
            ids.append(value)
    return ids, rejected


def sequence_ids(prefix: str, start: int, count: int, width: int = 0) -> list[str]:
    """prefix + start, start+1, ... zero-padded to `width` digits."""
    if count < 1 or start < 0:
        raise CoverSheetError("Enter a start number and a count of at least 1.")
    if count > MAX_IDS:
        raise CoverSheetError(f"At most {MAX_IDS} IDs at a time.")
    return [f"{prefix}{n:0{width}d}" for n in range(start, start + count)]


def _barcode(value: str, max_width: float, bar_height: float):
    """A Code128 flowable scaled to fit max_width, or an error when the ID
    is too long to print legibly in that width."""
    bar = code128.Code128(value, barWidth=1.2, barHeight=bar_height, quiet=True)
    if bar.width > max_width:
        scaled = 1.2 * max_width / bar.width
        if scaled < MIN_BAR_WIDTH:
            raise CoverSheetError(
                f"The ID {value[:20]!r} is too long to print as a readable "
                "barcode on this layout.")
        bar = code128.Code128(value, barWidth=scaled, barHeight=bar_height,
                              quiet=True)
    return bar


def write_cover_sheets(ids: list[str], out_path: str | Path,
                       layout: str = "cover", copies: int = 1,
                       title: str = "DOCUMENT COVER SHEET") -> Path:
    """Write the PDF. layout "cover": one A4 page per ID. layout "labels":
    sticker grid, `copies` of each ID."""
    if not ids:
        raise CoverSheetError("There are no IDs to print.")
    if len(ids) > MAX_IDS:
        raise CoverSheetError(f"At most {MAX_IDS} IDs at a time.")
    if layout not in ("cover", "labels"):
        raise CoverSheetError("Unknown layout.")
    copies = max(1, min(int(copies), MAX_COPIES))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(out), pagesize=A4)
    if layout == "cover":
        _cover_pages(c, ids, title)
    else:
        _label_pages(c, [i for i in ids for _ in range(copies)])
    c.save()
    return out


def _cover_pages(c, ids: list[str], title: str) -> None:
    width, height = A4
    for value in ids:
        c.setFont("Helvetica-Bold", 20)
        c.drawCentredString(width / 2, height - 90, title)
        bar = _barcode(value, width - 60 * mm, 70)
        bar.drawOn(c, (width - bar.width) / 2, height - 230)
        c.setFont("Helvetica-Bold", 22)
        c.drawCentredString(width / 2, height - 262, value)
        c.showPage()


def _label_pages(c, values: list[str]) -> None:
    width, height = A4
    cell_w, cell_h = width / LABEL_COLUMNS, 28 * mm
    top = height - 14 * mm
    per_page = LABEL_COLUMNS * LABEL_ROWS
    for index, value in enumerate(values):
        slot = index % per_page
        if index and slot == 0:
            c.showPage()
        col, row = slot % LABEL_COLUMNS, slot // LABEL_COLUMNS
        x, y = col * cell_w, top - (row + 1) * cell_h
        bar = _barcode(value, cell_w - 10 * mm, 14 * mm)
        bar.drawOn(c, x + (cell_w - bar.width) / 2, y + 7 * mm)
        c.setFont("Helvetica", 9)
        c.drawCentredString(x + cell_w / 2, y + 3 * mm, value)
    c.showPage()
