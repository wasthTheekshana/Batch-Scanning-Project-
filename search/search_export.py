"""Download what a search found as one CSV (opens in Excel).

Every matching document, not only the page of results on screen, up to
MAX_ROWS. Values are untrusted text, so cells a spreadsheet would run as a
formula are neutralised exactly as in the index files.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

from search.search import MAX_LIMIT, SearchQuery, search_documents
from export.index_export import _csv_text, _write_atomic

MAX_ROWS = 10_000
FIXED = ["document_id", "project", "scanned_at", "operator", "status", "delivery",
         "pages", "pdf_file", "barcode", "document_types"]


def build_rows(db, query: SearchQuery, max_rows: int = MAX_ROWS) -> tuple[list[str], list[dict], bool]:
    """(columns, rows, truncated) for everything the query matches."""
    q = dataclasses.replace(query, limit=MAX_LIMIT, offset=0)
    rows: list[dict] = []
    extra: list[str] = []
    truncated = False
    while True:
        page = search_documents(db, q)
        for doc in page:
            if len(rows) >= max_rows:
                truncated = True
                break
            fields = dict(doc["fields"])
            row = {"document_id": doc["document_id"], "project": doc["project_name"],
                   "scanned_at": doc["created_at"], "operator": doc["operator"] or "",
                   "status": doc["validation_status"], "delivery": doc["delivery_status"] or "",
                   "pages": doc["page_range"] or "",
                   "pdf_file": Path(doc["storage_path"]).name if doc["storage_path"] else "",
                   "barcode": fields.pop("barcode", ""),
                   "document_types": fields.pop("doc_types", "")}
            for name, value in fields.items():
                if name not in FIXED:                 # a field cannot overwrite a fixed column
                    row[name] = value
                    if name not in extra:
                        extra.append(name)
            rows.append(row)
        if truncated or len(page) < q.effective_limit:
            break
        q = dataclasses.replace(q, offset=q.offset + len(page))
    return FIXED + extra, rows, truncated


def export_csv(db, query: SearchQuery, path: str | Path,
               max_rows: int = MAX_ROWS) -> tuple[int, bool]:
    """Write the CSV; returns (documents written, truncated)."""
    columns, rows, truncated = build_rows(db, query, max_rows)
    text = _csv_text(columns, [{k: ("" if v is None else str(v)) for k, v in r.items()}
                               for r in rows])
    _write_atomic(Path(path), text, "utf-8-sig")
    return len(rows), truncated
