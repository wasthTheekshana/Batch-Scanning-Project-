"""Manual split and merge of documents (HLD: "manual split/merge of documents in the viewer").

The automatic separation (barcodes, blank pages, ...) is sometimes wrong: two applications
come out as one document, or one application is cut in two. An operator fixes that here.

Nothing is deleted. The original document is marked `rejected` with a note that it was
superseded and by which documents, and the new document(s) are created in `pending_qc`, so
they appear in the QC review queue; "Accept -> export" then builds and delivers their PDFs
through the normal export path. A PDF that was already delivered for the original stays
where it went (it is the record of what was sent); the result says so.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field


class DocumentEditError(ValueError):
    """The split or merge is not possible; the message says why."""


@dataclass
class EditResult:
    new_ids: list[str]
    superseded: list[str]
    already_delivered: list[str] = field(default_factory=list)   # originals whose PDF had been sent


def _pages(row) -> dict[str, dict]:
    try:
        detail = json.loads(row["qc_detail"] or "{}")
    except (TypeError, ValueError):
        return {}
    return {str(k): (v if isinstance(v, dict) else {}) for k, v in (detail.get("pages") or {}).items()}


def _detail(row) -> dict:
    try:
        return json.loads(row["qc_detail"] or "{}")
    except (TypeError, ValueError):
        return {}


def _fields(row) -> dict:
    try:
        return json.loads(row["extracted_fields"] or "{}")
    except (TypeError, ValueError):
        return {}


def page_numbers(row) -> list[int]:
    return sorted(int(n) for n in _pages(row) if str(n).isdigit())


def why_not_editable(row) -> str:
    """"" when the document can be split or merged, else why not."""
    if row is None:
        return "unknown document"
    if row["validation_status"] == "rejected":
        return "the document is rejected (or was already split or merged)"
    if not _pages(row):
        return "the document has no page record to work from"
    return ""


def _types_of(pages: dict[str, dict]) -> str:
    seen = [pages[n].get("doc_type") for n in sorted(pages, key=int) if pages[n].get("doc_type")]
    return ", ".join(dict.fromkeys(seen))


def _range(pages: dict[str, dict]) -> str:
    nos = sorted(int(n) for n in pages)
    return f"{nos[0]}-{nos[-1]}"


def _delivered(row) -> bool:
    try:
        details = json.loads(row["delivery_detail"] or "{}")
    except (TypeError, ValueError):
        details = {}
    return bool(row["storage_path"]) or any(
        isinstance(v, dict) and v.get("success") for v in details.values())


def _create(db, parent, pages: dict[str, dict], fields: dict, flags: list[str], source_ids: list[str]) -> str:
    new_id = str(uuid.uuid4())
    if _types_of(pages):
        fields = {**fields, "doc_types": _types_of(pages)}
    else:
        fields = {k: v for k, v in fields.items() if k != "doc_types"}
    detail = {"pages": pages, "flags": sorted(set(flags)), "created_by_edit": source_ids}
    db.insert_document(new_id, parent["batch_id"], parent["project_id"],
                       page_range=_range(pages), enhancement_profile="default",
                       extracted_fields=fields, validation_status="pending_qc",
                       qc_score=parent["qc_score"], qc_detail=detail)
    for bundle_id in db.bundles_of_document(parent["document_id"]):      # stays with its custody bundle
        db.link_bundle_document(bundle_id, new_id, parent["batch_id"])
    return new_id


def _supersede(db, row, new_ids: list[str], how: str) -> None:
    detail = _detail(row)
    detail["superseded_by"] = new_ids
    detail["superseded_how"] = how
    detail["flags"] = sorted(set(detail.get("flags", [])) | {"superseded"})
    db.update_document(row["document_id"], validation_status="rejected", qc_detail=detail)


def split_document(db, audit, user: str, document_id: str, first_page_of_second: int) -> EditResult:
    """Cut a document in two: pages before `first_page_of_second` stay in the first
    document, that page and the ones after it become the second."""
    row = db.get_document(document_id)
    problem = why_not_editable(row)
    if problem:
        raise DocumentEditError(problem)
    pages = _pages(row)
    numbers = page_numbers(row)
    if len(numbers) < 2:
        raise DocumentEditError("a one-page document cannot be split")
    if first_page_of_second not in numbers or first_page_of_second == numbers[0]:
        raise DocumentEditError("choose a page after the first one to start the second document")
    head = {str(n): pages[str(n)] for n in numbers if n < first_page_of_second}
    tail = {str(n): pages[str(n)] for n in numbers if n >= first_page_of_second}
    flags = list(_detail(row).get("flags", []))
    fields = _fields(row)
    first = _create(db, row, head, fields, flags, [document_id])
    # the second document starts without index values: they belonged to the first page's
    # content, and the Index tab will ask for them
    second = _create(db, row, tail, {k: v for k, v in fields.items() if k == "barcode"},
                     flags + ["low_confidence_index"], [document_id])
    result = EditResult([first, second], [document_id],
                        [document_id] if _delivered(row) else [])
    _supersede(db, row, result.new_ids, "split")
    if audit:
        audit.log("document_split", user, document_id=document_id,
                  at_page=first_page_of_second, new=result.new_ids)
    return result


def merge_documents(db, audit, user: str, first_id: str, second_id: str) -> EditResult:
    """Join two documents of one batch into one, pages in scan order."""
    if first_id == second_id:
        raise DocumentEditError("choose two different documents")
    a, b = db.get_document(first_id), db.get_document(second_id)
    for row in (a, b):
        problem = why_not_editable(row)
        if problem:
            raise DocumentEditError(problem)
    if a["batch_id"] != b["batch_id"] or a["project_id"] != b["project_id"]:
        raise DocumentEditError("only documents of the same batch can be merged")
    pages_a, pages_b = _pages(a), _pages(b)
    clash = set(pages_a) & set(pages_b)
    if clash:
        raise DocumentEditError("the documents share page numbers; they cannot be merged")
    pages = {**pages_a, **pages_b}
    pages = {n: pages[n] for n in sorted(pages, key=int)}
    first, second = sorted([a, b], key=lambda r: page_numbers(r)[0])
    fields = {**{k: v for k, v in _fields(second).items() if v}, **{k: v for k, v in _fields(first).items() if v}}
    flags = list(_detail(a).get("flags", [])) + list(_detail(b).get("flags", []))
    new_id = _create(db, first, pages, fields, flags, [first_id, second_id])
    for bundle_id in db.bundles_of_document(second["document_id"]):
        db.link_bundle_document(bundle_id, new_id, first["batch_id"])
    result = EditResult([new_id], [first_id, second_id],
                        [r["document_id"] for r in (a, b) if _delivered(r)])
    for row in (a, b):
        _supersede(db, row, [new_id], "merge")
    if audit:
        audit.log("document_merged", user, document_id=new_id, merged=[first_id, second_id])
    return result
