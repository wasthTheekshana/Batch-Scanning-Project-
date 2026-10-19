"""Admin: search the documents the stations have reported, open one, and see who looked.

A `searcher` sees only the projects they were given; an `auditor` sees all (read-only). Identifiers are found
through the blind index (exact match on a field such as the NIC or account number); nothing is stored in clear.
Every search and every opened document is written to the access log (the kind of filter, never its value).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from central.audit import record
from central.crypto import DataKey, DecryptionError
from central.deps import get_db, require_admin, require_csrf
from central.models import (AdminUser, Document, DocumentAccessLog, DocumentFile, IndexLookup, IndexRevision, Pc,
                            Project, SearcherScope, utcnow)
from central.routes_pc_documents import file_details

router = APIRouter(prefix="/admin/documents", tags=["admin-documents"],
                   dependencies=[Depends(require_admin), Depends(require_csrf)])

PAGE_MAX = 100
PREVIEW_FIELDS = 6
MIN_SEARCH = 3


def scope_of(db: Session, user: AdminUser) -> Optional[set[str]]:
    """The project ids this person may see; None means all of them."""
    if user.role != "searcher":
        return None
    return set(db.scalars(select(SearcherScope.project_id).where(SearcherScope.admin_id == user.id)))


def _key(request: Request) -> DataKey:
    key = request.app.state.settings.data_key
    if key is None:
        raise HTTPException(503, "the server has no data key configured (CENTRAL_DATA_KEY)")
    return key


def _log(db: Session, request: Request, user: AdminUser, action: str, document_id: Optional[str], detail: dict) -> None:
    db.add(DocumentAccessLog(username=user.username, document_id=document_id, action=action,
                             detail=json.dumps(detail, sort_keys=True), ip=(request.client.host if request.client else "")[:64]))


def _fields(key: DataKey, rev: IndexRevision) -> Optional[dict]:
    try:
        return json.loads(key.decrypt_text(rev.fields_enc, f"index:{rev.document_id}:{rev.revision}"))
    except (DecryptionError, ValueError):
        return None


def _day(value: Optional[str], name: str) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, f"{name} must be a date like 2026-10-18") from None


def _project_names(db: Session) -> dict[str, str]:
    return {p.id: p.name for p in db.scalars(select(Project))}


@router.get("/filters")
def filters(user: AdminUser = Depends(require_admin), db: Session = Depends(get_db)):
    """What the search form can offer: the projects and stations that have documents (within the person's scope)."""
    scope = scope_of(db, user)
    q = select(Document.project_id, Document.pc_id).where(Document.deleted_at.is_(None)).distinct()
    if scope is not None:
        q = q.where(Document.project_id.in_(scope))
    rows = db.execute(q).all()
    names, pcs = _project_names(db), {p.id: p.display_name for p in db.scalars(select(Pc))}
    return {"projects": sorted(({"id": p, "name": names.get(p, p)} for p in {r[0] for r in rows}), key=lambda x: x["name"]),
            "stations": sorted(({"id": i, "name": pcs.get(i, i)} for i in {r[1] for r in rows}), key=lambda x: x["name"]),
            "statuses": sorted(set(db.scalars(select(Document.validation_status).where(Document.deleted_at.is_(None))
                                              .distinct())) - {""})}


@router.get("/access-log")
def access_log(limit: int = Query(50, ge=1, le=PAGE_MAX), before_id: Optional[int] = None,
               user: AdminUser = Depends(require_admin), db: Session = Depends(get_db)):
    if user.role == "searcher":
        raise HTTPException(403, "not available to this account")
    q = select(DocumentAccessLog).order_by(DocumentAccessLog.id.desc()).limit(limit)
    if before_id:
        q = q.where(DocumentAccessLog.id < before_id)
    return {"items": [{"id": r.id, "at": r.at.isoformat(timespec="seconds") + "Z", "user": r.username,
                       "document_id": r.document_id, "action": r.action, "detail": json.loads(r.detail or "{}"),
                       "ip": r.ip} for r in db.scalars(q)]}


class Filters:
    """The search form's choices, as query parameters (shared by the search and the CSV export)."""

    def __init__(self, q: str = Query("", max_length=100), field: str = Query("", max_length=64),
                 value: str = Query("", max_length=100), project: str = Query("", max_length=64),
                 pc: str = Query("", max_length=36), batch: str = Query("", max_length=64),
                 status: str = Query("", max_length=30), date_from: str = "", date_to: str = ""):
        self.q, self.field, self.value, self.project, self.pc = q, field, value, project, pc
        self.batch, self.status, self.date_from, self.date_to = batch, status, date_from, date_to


def build_query(f: Filters, request: Request, user: AdminUser, db: Session):
    """The documents a person may see that match the form, and the kinds of filter used (never their values)."""
    q, field, value, project, pc, batch, status = f.q, f.field, f.value, f.project, f.pc, f.batch, f.status
    date_from, date_to = f.date_from, f.date_to
    scope = scope_of(db, user)
    query = select(Document).where(Document.deleted_at.is_(None))
    used: list[str] = []
    if scope is not None:
        query = query.where(Document.project_id.in_(scope))
    if q.strip():
        if len(DataKey.normalise(q)) < MIN_SEARCH:
            raise HTTPException(422, f"enter at least {MIN_SEARCH} letters or digits")
        key = _key(request)
        names = list(db.scalars(select(IndexLookup.field_name).distinct()))
        hashes = [h for n in names for h in key.blind_all(n, q)]
        query = query.where(Document.id.in_(select(IndexLookup.document_id).where(IndexLookup.blind_hash.in_(hashes or [""]))))
        used.append("any_field")
    if field.strip() or value.strip():
        if not (field.strip() and value.strip()):
            raise HTTPException(422, "give both the field and its value")
        if len(DataKey.normalise(value)) < MIN_SEARCH:
            raise HTTPException(422, f"enter at least {MIN_SEARCH} letters or digits")
        key = _key(request)
        query = query.where(Document.id.in_(select(IndexLookup.document_id).where(
            IndexLookup.field_name == field.strip(), IndexLookup.blind_hash.in_(key.blind_all(field.strip(), value) or [""]))))
        used.append("field:" + field.strip())
    if project:
        query = query.where(Document.project_id == project)
        used.append("project")
    if pc:
        query = query.where(Document.pc_id == pc)
        used.append("station")
    if batch:
        query = query.where(Document.batch_id == batch)
        used.append("batch")
    if status:
        query = query.where(Document.validation_status == status)
        used.append("status")
    start, end = _day(date_from, "date_from"), _day(date_to, "date_to")
    if start:
        query = query.where(Document.created_at >= datetime.combine(start, datetime.min.time()))
        used.append("date_from")
    if end:
        query = query.where(Document.created_at < datetime.combine(end + timedelta(days=1), datetime.min.time()))
        used.append("date_to")
    return query, used


@router.get("")
def search(request: Request, f: Filters = Depends(),
           limit: int = Query(25, ge=1, le=PAGE_MAX), offset: int = Query(0, ge=0, le=100_000),
           user: AdminUser = Depends(require_admin), db: Session = Depends(get_db)):
    query, used = build_query(f, request, user, db)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(db.scalars(query.order_by(Document.created_at.desc(), Document.id).limit(limit).offset(offset)))
    key = request.app.state.settings.data_key
    revisions = {r.document_id: r for r in db.scalars(select(IndexRevision).where(
        IndexRevision.document_id.in_([d.id for d in rows] or [""]), IndexRevision.is_current.is_(True)))}
    pcs, names = {p.id: p.display_name for p in db.scalars(select(Pc))}, _project_names(db)
    items = []
    for d in rows:
        rev = revisions.get(d.id)
        fields = _fields(key, rev) if (key is not None and rev is not None) else None
        items.append({"id": d.id, "pc_id": d.pc_id, "pc_name": pcs.get(d.pc_id, ""), "project_id": d.project_id,
                      "project_name": names.get(d.project_id, d.project_id), "batch_id": d.batch_id,
                      "page_count": d.page_count, "validation_status": d.validation_status,
                      "created_at": d.created_at.isoformat(timespec="seconds") + "Z", "revision": d.current_revision,
                      "fields": dict(list(fields.items())[:PREVIEW_FIELDS]) if fields is not None else None})
    _log(db, request, user, "search", None, {"filters": used, "found": total})
    db.commit()
    return {"total": total, "limit": limit, "offset": offset, "items": items}


EXPORT_MAX = 5000
FORMULA_STARTS = ("=", "+", "-", "@", chr(9), chr(13))


def _cell(value) -> str:
    """A spreadsheet runs text that starts with = + - @ as a formula; a leading quote stops that."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in FORMULA_STARTS else text


@router.get("/export.csv")
def export(request: Request, f: Filters = Depends(), user: AdminUser = Depends(require_admin),
           db: Session = Depends(get_db)):
    """The documents the search form selects, as a spreadsheet (at most 5,000 rows). Recorded in the access log."""
    import csv
    import io

    from fastapi.responses import Response
    key = _key(request)
    query, used = build_query(f, request, user, db)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(db.scalars(query.order_by(Document.created_at.desc(), Document.id).limit(EXPORT_MAX)))
    revisions = {r.document_id: r for r in db.scalars(select(IndexRevision).where(
        IndexRevision.document_id.in_([d.id for d in rows] or [""]), IndexRevision.is_current.is_(True)))}
    pcs, names = {p.id: p.display_name for p in db.scalars(select(Pc))}, _project_names(db)
    parsed = {d.id: (_fields(key, revisions[d.id]) if d.id in revisions else {}) or {} for d in rows}
    columns = sorted({name for fields in parsed.values() for name in fields})
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([_cell(h) for h in ("scanned_at", "station", "project", "batch", "status", "pages", "revision", "document_id", *columns)])
    for d in rows:
        writer.writerow([_cell(x) for x in (d.created_at.isoformat(timespec="seconds") + "Z", pcs.get(d.pc_id, ""),
                                            names.get(d.project_id, d.project_id), d.batch_id, d.validation_status,
                                            d.page_count, d.current_revision, d.id,
                                            *(parsed[d.id].get(c, "") for c in columns))])
    _log(db, request, user, "export", None, {"filters": used, "rows": len(rows), "matched": total})
    db.commit()
    return Response(out.getvalue(), media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": 'attachment; filename="documents.csv"',
        "X-Rows": str(len(rows)), "X-Matched": str(total)})


class DeleteIn(BaseModel):
    reason: str = Field(min_length=5, max_length=300)


@router.post("/{document_id}/delete")
def delete_document(document_id: str, body: DeleteIn, request: Request, user: AdminUser = Depends(require_admin),
                    db: Session = Depends(get_db)):
    """Remove a document's index data and file details from the server (administrators only; the PDF is not held
    here, so it stays wherever it was delivered). The reason is recorded. A marker stays, so the station does not
    send the document again; the station's own copy is not touched."""
    doc = db.get(Document, document_id)
    if doc is None or doc.deleted_at is not None:
        raise HTTPException(404, "no such document")
    if user.role != "admin":
        raise HTTPException(403, "only an administrator can delete documents")
    revisions = db.scalar(select(func.count()).select_from(IndexRevision).where(IndexRevision.document_id == doc.id)) or 0
    db.execute(delete(IndexLookup).where(IndexLookup.document_id == doc.id))
    db.execute(delete(IndexRevision).where(IndexRevision.document_id == doc.id))
    db.execute(delete(DocumentFile).where(DocumentFile.document_id == doc.id))
    doc.deleted_at, doc.deleted_by, doc.current_revision, doc.validation_status = utcnow(), user.username, 0, ""
    record(db, f"admin:{user.username}", "document_deleted",
           {"document": doc.id, "pc_id": doc.pc_id, "project_id": doc.project_id, "revisions": revisions,
            "reason": body.reason.strip()})
    _log(db, request, user, "delete", doc.id, {})
    db.commit()
    return {"deleted": True, "document": doc.id}


@router.get("/{document_id}")
def document(document_id: str, request: Request, user: AdminUser = Depends(require_admin),
             db: Session = Depends(get_db)):
    key = _key(request)
    doc = db.get(Document, document_id)
    scope = scope_of(db, user)
    if doc is None or doc.deleted_at is not None or (scope is not None and doc.project_id not in scope):
        raise HTTPException(404, "no such document")                  # outside the scope looks the same as absent
    pc = db.get(Pc, doc.pc_id)
    project = db.get(Project, doc.project_id)
    revisions = list(db.scalars(select(IndexRevision).where(IndexRevision.document_id == doc.id)
                                .order_by(IndexRevision.revision)))
    history, previous = [], {}
    for rev in revisions:
        fields = _fields(key, rev)
        changes = None
        if fields is not None:
            changes = [{"field": n, "before": previous.get(n), "after": fields.get(n)}
                       for n in sorted(set(previous) | set(fields)) if previous.get(n) != fields.get(n)]
            previous = fields
        history.append({"revision": rev.revision, "received_at": rev.received_at.isoformat(timespec="seconds") + "Z",
                        "edited_at": rev.edited_at.isoformat(timespec="seconds") + "Z" if rev.edited_at else None,
                        "validation_status": rev.validation_status, "project_version": rev.project_version,
                        "source": rev.source, "is_current": rev.is_current, "changes": changes})
    current = next((r for r in reversed(revisions) if r.is_current), None)
    files = []
    for f in db.scalars(select(DocumentFile).where(DocumentFile.document_id == doc.id).order_by(DocumentFile.id.desc())):
        try:
            details = file_details(key, doc.id, f.details_enc)
        except (DecryptionError, ValueError):
            details = {"file_name": "", "delivered_to": []}
        files.append({"file_name": details.get("file_name", ""), "delivered_to": details.get("delivered_to", []),
                      "size": f.size, "sha256": f.sha256, "delivery_status": f.delivery_status,
                      "file_state": f.file_state, "is_current": f.is_current,
                      "received_at": f.received_at.isoformat(timespec="seconds") + "Z"})
    _log(db, request, user, "view", doc.id, {})
    db.commit()
    return {"id": doc.id, "source_document_id": doc.source_document_id, "pc_id": doc.pc_id,
            "pc_name": pc.display_name if pc else "", "project_id": doc.project_id,
            "project_name": project.name if project else doc.project_id, "batch_id": doc.batch_id,
            "page_count": doc.page_count, "validation_status": doc.validation_status,
            "created_at": doc.created_at.isoformat(timespec="seconds") + "Z",
            "received_at": doc.received_at.isoformat(timespec="seconds") + "Z", "revision": doc.current_revision,
            "fields": _fields(key, current) if current is not None else {},
            "fields_unreadable": current is not None and _fields(key, current) is None,
            "files": files, "history": list(reversed(history))}
