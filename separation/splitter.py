"""Document separation orchestrator (HLD §4 Layer 4).

Applies the strategies enabled in project separation config, in priority
order: fixed_pages (exclusive) → barcode / zone (page starts a new
document) → blank_page (separator page removed). Input pages are dicts
prepared by the pipeline:

    {"page_no": int, "image": ndarray | None,
     "is_blank": bool, "barcodes": [{"type","value","rect"}, ...],
     "ocr_text": str}

Strategy "document_type": a page whose document type is marked
`starts_document` (the title page of a form, matched by its `regex`) begins
a new document, so a stack with no barcodes is cut into one document per form.
The pipeline sets page["starts_document"].

separation.drop_blank_pages discards EMPTY pages (ink density under
drop_blank_threshold, default 0.4%) without splitting; sparse pages are kept.
Ignored when the blank_page strategy, which splits on blanks, is on.

separation.barcode_role says what a barcode page is: "first_page" (default:
the page is the document's first page and stays in it), "cover_sheet" (a
separator sheet that labels the document but is not part of it) or "auto"
(a cover when the page has hardly any text besides the barcode).

Returns a list of documents: {"pages": [page, ...], "barcode": str | None}.
"""
from __future__ import annotations

import re
from typing import Callable

import numpy as np

from separation.zone_split import check_zone_trigger


def split_documents(pages: list[dict], separation_cfg: dict,
                    ocr_fn: Callable[[np.ndarray], str] | None = None) -> list[dict]:
    strategies = separation_cfg.get("strategy", [])
    if not pages:
        return []

    if separation_cfg.get("drop_blank_pages") and "blank_page" not in strategies:
        # discard EMPTY pages (the blank back of a one-sided sheet scanned
        # duplex) WITHOUT splitting: unlike the blank_page strategy they are
        # not separators. This is deliberately far stricter than the
        # separator test: a sparse page (an ID card, a short note) is
        # content, and losing it silently would be worse than keeping a
        # blank. A page carrying a barcode is never dropped.
        limit = float(separation_cfg.get("drop_blank_threshold", 0.004))
        pages = [p for p in pages
                 if not (p.get("is_blank") and (p.get("ink_density") is None
                                               or p["ink_density"] < limit))
                 or p.get("barcodes") or p.get("is_custody_label")]
        if not pages:
            return []

    if "fixed_pages" in strategies and separation_cfg.get("fixed_pages"):
        n = int(separation_cfg["fixed_pages"])
        return [_doc(pages[i:i + n]) for i in range(0, len(pages), n)]

    use_barcode = "barcode" in strategies
    use_blank = "blank_page" in strategies
    use_zone = "zone" in strategies and ocr_fn is not None
    use_type = "document_type" in strategies
    zones = separation_cfg.get("zones", [])

    docs: list[dict] = []
    current: list[dict] = []
    cover_code: str | None = None      # barcode of a cover sheet awaiting pages

    def close() -> None:
        nonlocal current, cover_code
        if current:
            docs.append(_doc(current, cover_code))
            current, cover_code = [], None

    for page in pages:
        if page.get("is_custody_label"):
            close()
            continue  # custody QR label sheet, never document content

        if use_blank and page.get("is_blank"):
            close()
            continue  # separator sheet dropped

        starts_new = False
        if use_barcode and page.get("barcodes"):
            starts_new = True
            if _is_cover(page, separation_cfg):
                close()
                cover_code = page["barcodes"][0]["value"]
                continue  # the cover labels the next document, it is not in it
        elif use_zone and page.get("image") is not None:
            starts_new = any(check_zone_trigger(page["image"], z, ocr_fn)
                             for z in zones)

        if use_type and page.get("starts_document"):
            starts_new = True        # the title page of a form begins a document

        if starts_new:
            close()
        current.append(page)

    close()
    return docs


def _is_cover(page: dict, cfg: dict) -> bool:
    role = cfg.get("barcode_role", "first_page")
    if role == "cover_sheet":
        return True
    if role != "auto":
        return False
    text = page.get("ocr_text") or ""
    for code in page.get("barcodes", []):
        text = text.replace(code["value"], " ")
    return len(re.sub(r"\W", "", text)) < int(cfg.get("cover_max_chars", 40))


def _doc(pages: list[dict], cover_code: str | None = None) -> dict:
    barcode = cover_code
    for p in pages:
        if barcode is None and p.get("barcodes"):
            barcode = p["barcodes"][0]["value"]
    return {"pages": pages, "barcode": barcode}
