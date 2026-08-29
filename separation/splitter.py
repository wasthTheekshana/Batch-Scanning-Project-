"""Document separation orchestrator (HLD §4 Layer 4).

Applies the strategies enabled in project separation config, in priority
order: fixed_pages (exclusive) → barcode / zone (page starts a new
document) → blank_page (separator page removed). Input pages are dicts
prepared by the pipeline:

    {"page_no": int, "image": ndarray | None,
     "is_blank": bool, "barcodes": [{"type","value","rect"}, ...]}

Returns a list of documents: {"pages": [page, ...], "barcode": str | None}.
"""
from __future__ import annotations

from typing import Callable

import numpy as np

from separation.zone_split import check_zone_trigger


def split_documents(pages: list[dict], separation_cfg: dict,
                    ocr_fn: Callable[[np.ndarray], str] | None = None) -> list[dict]:
    strategies = separation_cfg.get("strategy", [])
    if not pages:
        return []

    if "fixed_pages" in strategies and separation_cfg.get("fixed_pages"):
        n = int(separation_cfg["fixed_pages"])
        return [_doc(pages[i:i + n]) for i in range(0, len(pages), n)]

    use_barcode = "barcode" in strategies
    use_blank = "blank_page" in strategies
    use_zone = "zone" in strategies and ocr_fn is not None
    zones = separation_cfg.get("zones", [])

    docs: list[dict] = []
    current: list[dict] = []
    for page in pages:
        if page.get("is_custody_label"):
            if current:
                docs.append(_doc(current))
                current = []
            continue  # custody QR label sheet, never document content

        if use_blank and page.get("is_blank"):
            if current:
                docs.append(_doc(current))
                current = []
            continue  # separator sheet dropped

        starts_new = False
        if use_barcode and page.get("barcodes"):
            starts_new = True
        elif use_zone and page.get("image") is not None:
            starts_new = any(check_zone_trigger(page["image"], z, ocr_fn)
                             for z in zones)

        if starts_new and current:
            docs.append(_doc(current))
            current = []
        current.append(page)

    if current:
        docs.append(_doc(current))
    return docs


def _doc(pages: list[dict]) -> dict:
    barcode = None
    for p in pages:
        if p.get("barcodes"):
            barcode = p["barcodes"][0]["value"]
            break
    return {"pages": pages, "barcode": barcode}
