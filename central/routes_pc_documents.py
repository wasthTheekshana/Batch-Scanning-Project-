"""PC-facing: a PC reports each finished document's details and its index fields.

Central keeps the index data (encrypted at rest, with a blind index for exact-match search) and the PDF's
details (name, size, SHA-256, where it was delivered). It does not keep the PDF. Both calls are idempotent: a PC
that never saw the reply simply sends again. Design: docs/superpowers/specs/2026-10-15-central-document-sync-design.md
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from central.audit import record
from central.crypto import DataKey
from central.deps import get_db, require_pc
from central.models import Document, DocumentFile, IndexLookup, IndexRevision, Pc, PcAssignment, utcnow

router = APIRouter(prefix="/pcs", tags=["pc-documents"])

ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
FIELD_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")      # never starts with a character a spreadsheet treats as a formula
MAX_FIELDS, MAX_VALUE = 100, 2000
LOOKUP_MIN, LOOKUP_MAX = 3, 64          # a value that can be an identifier; long free text is not searched by value


def data_key(request: Request) -> DataKey:
    key = request.app.state.settings.data_key
    if key is None:
        # the PC treats a 5xx as "try again later": nothing is lost, it waits for the server to be set up
        raise HTTPException(503, "the server has no data key configured (CENTRAL_DATA_KEY)")
    return key


def parse_time(value: str) -> datetime:
    """A PC's timestamp as naive UTC."""
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("not a date") from None
    if d.tzinfo is not None:
        d = d.astimezone(timezone.utc).replace(tzinfo=None)
    if d > utcnow() + timedelta(days=2):
        raise ValueError("a date in the future")
    return d


class MetaIn(BaseModel):
    project_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    batch_id: str = Field(pattern=ID.pattern)
    page_count: int = Field(ge=0, le=100_000)
    created_at: str = Field(max_length=40)
    validation_status: str = Field(default="", max_length=30)
    file_name: Optional[str] = Field(default=None, max_length=255)
    size: Optional[int] = Field(default=None, ge=0, le=10 ** 12)
    sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    delivered_to: list[str] = Field(default_factory=list, max_length=20)
    delivery_status: str = Field(default="", max_length=30)

    @field_validator("created_at")
    @classmethod
    def _created(cls, value):
        parse_time(value)
        return value

    @field_validator("delivered_to")
    @classmethod
    def _where(cls, value):
        if any(len(v) > 500 for v in value):
            raise ValueError("a destination is too long")
        return value


class IndexIn(BaseModel):
    fields: dict[str, str] = Field(max_length=MAX_FIELDS)
    validation_status: str = Field(default="", max_length=30)
    project_version: Optional[int] = Field(default=None, ge=0, le=10 ** 9)
    edited_at: Optional[str] = Field(default=None, max_length=40)

    @field_validator("fields")
    @classmethod
    def _fields(cls, value):
        for name, text in value.items():
            if not FIELD_NAME.match(name):
                raise ValueError("a field name is not valid")
            if len(text) > MAX_VALUE:
                raise ValueError("a value is too long")
        return value

    @field_validator("edited_at")
    @classmethod
    def _edited(cls, value):
        if value is not None:
            parse_time(value)
        return value


def _document(db: Session, pc: Pc, source_id: str) -> Document | None:
    return db.scalar(select(Document).where(Document.pc_id == pc.id, Document.source_document_id == source_id))


def _check_id(source_id: str) -> None:
    if not ID.match(source_id):
        raise HTTPException(422, "the document id is not valid")


def file_details(key: DataKey, doc_id: str, text: str) -> dict:
    return json.loads(key.decrypt_text(text, f"file:{doc_id}"))


@router.put("/{pc_id}/documents/{document_id}/meta")
def put_meta(document_id: str, body: MetaIn, request: Request, pc: Pc = Depends(require_pc),
             db: Session = Depends(get_db)):
    """The document's details. Same details again: nothing changes. A different PDF (re-exported after a
    correction) becomes the current file record; the earlier one is kept."""
    _check_id(document_id)
    key = data_key(request)
    now = utcnow()
    pc.last_seen_at = now
    created = parse_time(body.created_at)
    # a station files documents only under projects it was given: otherwise one station could put records into
    # another project's results (and in front of the people scoped to that project)
    if db.get(PcAssignment, (pc.id, body.project_id)) is None:
        raise HTTPException(422, "this project is not assigned to this station")
    doc = _document(db, pc, document_id)
    new = doc is None
    if doc is None:
        doc = Document(id=str(uuid.uuid4()), pc_id=pc.id, source_document_id=document_id, project_id=body.project_id,
                       batch_id=body.batch_id, page_count=body.page_count, created_at=created, received_at=now,
                       updated_at=now, validation_status=body.validation_status)
        db.add(doc)
        db.flush()
    changed = new
    if not new and (doc.project_id, doc.batch_id, doc.page_count, doc.validation_status) != (
            body.project_id, body.batch_id, body.page_count, body.validation_status):
        doc.project_id, doc.batch_id, doc.page_count = body.project_id, body.batch_id, body.page_count
        doc.validation_status, doc.updated_at = body.validation_status, now
        changed = True
    if body.sha256 is not None:
        current = db.scalar(select(DocumentFile).where(DocumentFile.document_id == doc.id,
                                                       DocumentFile.is_current.is_(True)))
        wanted = {"file_name": body.file_name or "", "delivered_to": body.delivered_to}
        sealed = key.encrypt(json.dumps(wanted), f"file:{doc.id}")
        if current is None or current.sha256 != body.sha256:
            if current is not None:
                current.is_current = False
            db.add(DocumentFile(document_id=doc.id, size=body.size or 0, sha256=body.sha256,
                                delivery_status=body.delivery_status, details_enc=sealed, received_at=now))
            changed = True
        elif current.delivery_status != body.delivery_status or file_details(key, doc.id, current.details_enc) != wanted:
            current.delivery_status, current.details_enc = body.delivery_status, sealed     # where it went has changed
            changed = True
    if changed:
        record(db, f"pc:{pc.id}", "document_received" if new else "document_updated", {"document": doc.id})
    db.commit()
    return {"status": "stored" if new else ("updated" if changed else "unchanged"), "file_state": "metadata_only"}


@router.put("/{pc_id}/documents/{document_id}/index")
def put_index(document_id: str, body: IndexIn, request: Request, pc: Pc = Depends(require_pc),
              db: Session = Depends(get_db)):
    """The document's index fields. The same fields as the current revision: a no-op. Different fields:
    the next revision (the earlier ones are kept)."""
    _check_id(document_id)
    key = data_key(request)
    doc = _document(db, pc, document_id)
    if doc is None:
        raise HTTPException(409, "send the document's details first")
    now = utcnow()
    pc.last_seen_at = now
    canonical = json.dumps(body.fields, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = key.fingerprint(f"fields:{doc.id}", canonical)
    current = db.scalar(select(IndexRevision).where(IndexRevision.document_id == doc.id,
                                                    IndexRevision.is_current.is_(True)))
    if current is not None and current.fields_hash == digest:
        if current.validation_status != body.validation_status:
            current.validation_status = doc.validation_status = body.validation_status
            db.commit()
        return {"status": "unchanged", "revision": current.revision}
    revision = doc.current_revision + 1
    if current is not None:
        current.is_current = False
    db.add(IndexRevision(
        document_id=doc.id, revision=revision, fields_enc=key.encrypt(canonical, f"index:{doc.id}:{revision}"),
        fields_hash=digest, validation_status=body.validation_status, project_version=body.project_version,
        edited_at=parse_time(body.edited_at) if body.edited_at else None, received_at=now, source="pc"))
    doc.current_revision, doc.updated_at, doc.validation_status = revision, now, body.validation_status
    db.execute(delete(IndexLookup).where(IndexLookup.document_id == doc.id))
    for name, text in body.fields.items():
        norm = DataKey.normalise(text)
        if LOOKUP_MIN <= len(norm) <= LOOKUP_MAX:
            db.add(IndexLookup(document_id=doc.id, field_name=name, blind_hash=key.blind(name, text)))
    record(db, f"pc:{pc.id}", "index_received", {"document": doc.id, "revision": revision})
    db.commit()
    return {"status": "stored", "revision": revision}
