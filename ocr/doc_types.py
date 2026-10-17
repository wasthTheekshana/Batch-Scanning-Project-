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

A type may set its own "mode" ("zone": regions drawn on the form, "full_page":
text patterns); without one it uses the project's ocr.mode. A customer file
can therefore hold a boxed bank form (read by region) next to an ID card or
letter (read by text pattern).
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)


def document_types(ocr_cfg: dict) -> list[dict]:
    return [t for t in (ocr_cfg or {}).get("document_types") or []
            if isinstance(t, dict) and t.get("name")]


def _matches(pattern, text: str, name: str) -> bool:
    if not pattern:
        return False
    try:
        return bool(re.search(pattern, text or "", re.IGNORECASE))
    except re.error:
        log.warning("document type %r has an invalid pattern; skipped", name)
        return False


def match_page(text: str, ocr_cfg: dict) -> tuple[dict | None, bool]:
    """(the page's document type, whether its fields are read from this page).
    Types are tried in order; for each, its `regex` finds the page that carries
    the fields, and an optional `continuation_regex` recognises the other pages
    of the same document (a form's later pages), typed but not read. The first
    type that claims the page wins, so a form's later pages are not taken for
    some other type that merely mentions the same words."""
    for doc_type in document_types(ocr_cfg):
        name = doc_type.get("name")
        if _matches(doc_type.get("regex"), text, name):
            return doc_type, True
        if _matches(doc_type.get("continuation_regex"), text, name):
            return doc_type, False
    return None, False


def classify_page(text: str, ocr_cfg: dict) -> dict | None:
    """The first document type that claims this page's text."""
    return match_page(text, ocr_cfg)[0]


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


def page_zone_groups(ocr_cfg: dict, text: str
                     ) -> tuple[list[tuple[list[dict], str]], str | None]:
    """([(zones, mode), ...], matched type name or None): the zones to read
    from this page, grouped by how they are read. Common zones use the
    project's mode, a type's zones use the type's own mode if it has one."""
    project_mode = (ocr_cfg or {}).get("mode") or "zone"
    common = list((ocr_cfg or {}).get("zones") or [])
    doc_type, reads_fields = match_page(text, ocr_cfg)
    if doc_type is None:
        return ([(common, project_mode)] if common else []), None
    if not reads_fields:                 # a later page of a form: typed only
        return [], doc_type["name"]
    own = list(doc_type.get("zones") or [])
    own_names = {z.get("name") for z in own}
    groups = []
    kept = [z for z in common if z.get("name") not in own_names]
    if kept:
        groups.append((kept, project_mode))
    if own:
        if doc_type.get("anchors"):      # lets the reader line zones up with the scan
            own = [{**z, "anchors": doc_type["anchors"]} for z in own]
        groups.append((own, doc_type.get("mode") or project_mode))
    return groups, doc_type["name"]


def enhancement_for(ocr_cfg: dict, base: dict | None, type_name: str | None) -> dict:
    """The image enhancement settings for a page of this document type: the project's
    settings, with whatever the type itself overrides (a handwritten form may want gentler
    treatment than a printed invoice)."""
    profile = dict(base or {})
    if type_name:
        for doc_type in document_types(ocr_cfg):
            if doc_type.get("name") == type_name:
                profile.update({k: v for k, v in (doc_type.get("enhancement_profile") or {}).items()
                                if k != "target_dpi"})
                break
    return profile


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
