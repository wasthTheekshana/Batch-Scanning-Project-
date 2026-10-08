"""Admin: register PCs and manage their enrollment codes / API keys."""
from datetime import datetime, timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from central.audit import record
from central.deps import get_db, require_admin, require_csrf
from central.models import (AdminUser, EnrollmentCode, Pc, PcAssignment,
                            utcnow)
from central.security import hash_secret, new_enrollment_code
from central.textsafe import SafeStr

router = APIRouter(prefix="/admin", tags=["admin-pcs"],
                   dependencies=[Depends(require_admin),
                                 Depends(require_csrf)])


class PcIn(BaseModel):
    display_name: SafeStr = Field(min_length=1, max_length=200)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def issue_code(db: Session, pc: Pc, settings) -> tuple[str, datetime]:
    """One live enrollment code per PC: issuing replaces any earlier one."""
    db.execute(delete(EnrollmentCode).where(EnrollmentCode.pc_id == pc.id))
    code = new_enrollment_code()
    expires = utcnow() + timedelta(minutes=settings.enrollment_code_ttl_minutes)
    db.add(EnrollmentCode(code_hash=hash_secret(code), pc_id=pc.id,
                          expires_at=expires))
    return code, expires


def _get_pc(db: Session, pc_id: str) -> Pc:
    pc = db.get(Pc, pc_id)
    if pc is None:
        raise HTTPException(404, "unknown PC")
    return pc


@router.post("/pcs", status_code=201)
def create_pc(body: PcIn, request: Request,
              admin: AdminUser = Depends(require_admin),
              db: Session = Depends(get_db)):
    if db.scalar(select(Pc).where(Pc.display_name == body.display_name)):
        raise HTTPException(409, "a PC with that name already exists")
    pc = Pc(id=str(uuid4()), display_name=body.display_name,
            created_at=utcnow())
    db.add(pc)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "a PC with that name already exists")
    code, expires = issue_code(db, pc, request.app.state.settings)
    record(db, f"admin:{admin.username}", "pc_created", {"pc_id": pc.id})
    db.commit()
    return {"pc_id": pc.id, "display_name": pc.display_name,
            "enrollment_code": code, "expires_at": _iso(expires)}


@router.get("/pcs")
def list_pcs(db: Session = Depends(get_db)):
    assigned: dict[str, list[str]] = {}
    for a in db.scalars(select(PcAssignment)):
        assigned.setdefault(a.pc_id, []).append(a.project_id)
    return [{"id": p.id, "display_name": p.display_name,
             "registered": p.api_key_hash is not None,
             "registered_at": _iso(p.registered_at),
             "last_seen_at": _iso(p.last_seen_at),
             "assigned_project_ids": sorted(assigned.get(p.id, []))}
            for p in db.scalars(select(Pc).order_by(Pc.display_name))]


@router.post("/pcs/{pc_id}/enrollment-code")
def new_code(pc_id: str, request: Request,
             admin: AdminUser = Depends(require_admin),
             db: Session = Depends(get_db)):
    pc = _get_pc(db, pc_id)
    code, expires = issue_code(db, pc, request.app.state.settings)
    record(db, f"admin:{admin.username}", "enrollment_code_issued",
           {"pc_id": pc.id})
    db.commit()
    return {"pc_id": pc.id, "enrollment_code": code,
            "expires_at": _iso(expires)}


@router.delete("/pcs/{pc_id}/key", status_code=204)
def revoke_key(pc_id: str, admin: AdminUser = Depends(require_admin),
               db: Session = Depends(get_db)):
    pc = _get_pc(db, pc_id)
    pc.api_key_hash = None
    record(db, f"admin:{admin.username}", "pc_key_revoked", {"pc_id": pc.id})
    db.commit()
    return Response(status_code=204)
