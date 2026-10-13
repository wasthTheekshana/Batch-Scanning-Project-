"""Document types inside one project (e.g. NIC, Application, Letter).

A customer's file is one PDF holding several kinds of page. Each type has a
detection pattern (matched, case-insensitively, against the page's OCR text)
and its own index zones:

    ocr: {
      "zones": [...],                      # common fields, tried on every page
      "document_types": [
        {"name": "NIC", "regex": "national identity card",
         "zones": [{"name": "nic_number", "regex": "..."}]},
        ...]}

A page takes the FIRST type whose pattern matches. Its fields are the common
zones plus that type's zones. Field names should be unique across types;
the document's fields are merged across its pages.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)


def document_types(ocr_cfg: dict) -> list[dict]:
    return [t for t in (ocr_cfg or {}).get("document_types") or []
            if isinstance(t, dict) and t.get("name")]


def classify_page(text: str, ocr_cfg: dict) -> dict | None:
    """The first document type whose pattern matches this page's text."""
    for doc_type in document_types(ocr_cfg):
        pattern = doc_type.get("regex")
        if not pattern:
            continue
        try:
            if re.search(pattern, text or "", re.IGNORECASE):
                return doc_type
        except re.error:
            log.warning("document type %r has an invalid pattern; skipped",
                        doc_type.get("name"))
    return None


def page_zones(ocr_cfg: dict, text: str) -> tuple[list[dict], str | None]:
    """(zones to extract from this page, matched type name or None)."""
    common = list((ocr_cfg or {}).get("zones") or [])
    doc_type = classify_page(text, ocr_cfg)
    if doc_type is None:
        return common, None
    own = list(doc_type.get("zones") or [])
    own_names = {z.get("name") for z in own}
    return [z for z in common if z.get("name") not in own_names] + own, \
        doc_type["name"]


def all_zones(ocr_cfg: dict) -> list[dict]:
    """Every field the project can produce (common + each type's), first
    definition of a name wins. This is the Index tab's form."""
    seen: set[str] = set()
    zones = []
    sources = [(ocr_cfg or {}).get("zones") or []]
    sources += [t.get("zones") or [] for t in document_types(ocr_cfg)]
    for source in sources:
        for zone in source:
            name = zone.get("name")
            if name and name not in seen:
                seen.add(name)
                zones.append(zone)
    return zones


def inherit_types(page_types: list[str | None]) -> list[str | None]:
    """A page with no type of its own (the back of an ID card, page 2 of a
    form) belongs to the type of the page before it in the same document."""
    out, last = [], None
    for name in page_types:
        last = name or last
        out.append(last)
    return out


def type_runs(page_types: list[str | None]) -> list[tuple[int, str]]:
    """[(first page index, type)] for each run of pages of one type, for
    PDF bookmarks."""
    runs, previous = [], None
    for index, name in enumerate(page_types):
        if name and name != previous:
            runs.append((index, name))
        previous = name
    return runs
