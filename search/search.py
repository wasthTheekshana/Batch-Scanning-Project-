"""Finding scanned documents: by what is written in their index fields, by
project, status and date.

Index values are stored as JSON on the document row. Matching looks at the
VALUES only (a search for "nic" must not match every document that has a field
called nic_number), uses bound parameters throughout, and survives a row whose
stored fields are not valid JSON (it is listed, but matches nothing by value).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta

MAX_LIMIT = 500
_HIDDEN_FIELDS = {"barcode", "doc_types"}          # shown in their own columns


def _like(term: str) -> str:
    """A LIKE pattern matching the term anywhere, % and _ taken literally."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _path(name: str) -> str:
    """A JSON path for a field name, quoted so the name cannot alter it."""
    return '$."' + str(name).replace('"', "").replace("\\", "") + '"'


@dataclass
class SearchQuery:
    text: str = ""
    field_filters: dict[str, str] = field(default_factory=dict)
    project_id: str | None = None
    validation_status: str | None = None
    delivery_status: str | None = None
    date_from: str | None = None          # YYYY-MM-DD, inclusive
    date_to: str | None = None            # YYYY-MM-DD, inclusive
    only_operator: str | None = None      # restrict to batches this user scanned
    limit: int = 200
    offset: int = 0

    @property
    def effective_limit(self) -> int:
        return max(1, min(int(self.limit), MAX_LIMIT))


def _where(q: SearchQuery) -> tuple[str, list]:
    clauses, args = [], []
    for term in q.text.split():
        clauses.append(
            "(d.document_id LIKE ? ESCAPE '\\' OR "
            "CASE WHEN json_valid(d.extracted_fields) THEN EXISTS("
            "SELECT 1 FROM json_each(d.extracted_fields) j "
            "WHERE j.value LIKE ? ESCAPE '\\') ELSE 0 END)")
        args += [_like(term), _like(term)]
    for name, value in (q.field_filters or {}).items():
        if not str(value).strip():
            continue
        clauses.append(
            "CASE WHEN json_valid(d.extracted_fields) THEN "
            "COALESCE(json_extract(d.extracted_fields, ?), '') LIKE ? ESCAPE '\\' "
            "ELSE 0 END")
        args += [_path(name), _like(str(value).strip())]
    if q.project_id:
        clauses.append("d.project_id = ?")
        args.append(q.project_id)
    if q.validation_status:
        clauses.append("d.validation_status = ?")
        args.append(q.validation_status)
    if q.delivery_status:
        clauses.append("d.delivery_status = ?")
        args.append(q.delivery_status)
    if q.date_from:
        clauses.append("d.created_at >= ?")
        args.append(q.date_from)
    if q.date_to:
        end = date.fromisoformat(q.date_to) + timedelta(days=1)
        clauses.append("d.created_at < ?")
        args.append(end.isoformat())
    if q.only_operator is not None:
        clauses.append("b.operator_user = ?")
        args.append(q.only_operator)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", args


_FROM = ("FROM documents d JOIN batches b ON b.batch_id = d.batch_id "
         "JOIN projects p ON p.project_id = d.project_id")


def search_documents(db, q: SearchQuery) -> list[dict]:
    where, args = _where(q)
    sql = ("SELECT d.document_id, d.project_id, p.name AS project_name, d.batch_id, "
           "b.operator_user AS operator, d.created_at, d.page_range, "
           "d.validation_status, d.delivery_status, d.storage_path, "
           "d.extracted_fields " + _FROM + where +
           " ORDER BY d.created_at DESC, d.rowid DESC LIMIT ? OFFSET ?")
    rows = db.query(sql, args + [q.effective_limit, max(0, int(q.offset))])
    results = []
    for row in rows:
        try:
            fields = json.loads(row["extracted_fields"] or "{}")
            fields = fields if isinstance(fields, dict) else {}
        except json.JSONDecodeError:
            fields = {}
        item = {k: row[k] for k in row.keys() if k != "extracted_fields"}
        item["fields"] = fields
        results.append(item)
    return results


def count_documents(db, q: SearchQuery) -> int:
    where, args = _where(q)
    return db.query("SELECT COUNT(*) AS n " + _FROM + where, args)[0]["n"]


def known_field_names(db, project_id: str | None = None,
                      sample: int = 500) -> list[str]:
    """Field names seen on recent documents (for the filter drop-down)."""
    sql = "SELECT extracted_fields FROM documents"
    args: list = []
    if project_id:
        sql += " WHERE project_id = ?"
        args.append(project_id)
    sql += " ORDER BY created_at DESC LIMIT ?"
    rows = db.query(sql, args + [sample])
    names: dict[str, None] = {}
    for row in rows:
        try:
            fields = json.loads(row["extracted_fields"] or "{}")
        except json.JSONDecodeError:
            continue
        if isinstance(fields, dict):
            for name in fields:
                if name not in _HIDDEN_FIELDS:
                    names.setdefault(name, None)
    return sorted(names)
