"""PC-facing endpoints: registration, heartbeat, config pull."""
import json
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from central.audit import record
from central.deps import get_db, require_pc
from central.models import EnrollmentCode, Pc, PcAssignment, Project, utcnow
from central.security import hash_secret, new_api_key, sanitize_log_value
from central.settings_store import get_poll_interval

router = APIRouter(prefix="/pcs", tags=["pcs"])


class RegisterIn(BaseModel):
    code: str = Field(min_length=8, max_length=100)


@router.post("/register")
def register(body: RegisterIn, request: Request,
             db: Session = Depends(get_db)):
    now = utcnow()
    h = hash_secret(body.code)
    # Claim the code atomically: update only if unused and not expired
    res = db.execute(update(EnrollmentCode)
        .where(EnrollmentCode.code_hash == h,
               EnrollmentCode.used_at.is_(None),
               EnrollmentCode.expires_at >= now)
        .values(used_at=now))
    if res.rowcount != 1:
        db.rollback()
        raise HTTPException(400, "invalid or expired enrollment code")
    row = db.get(EnrollmentCode, h)   # now ours; read pc_id
    pc = db.get(Pc, row.pc_id)
    if pc is None:   # dangling code -> uniform 400, not 500
        db.rollback()
        raise HTTPException(400, "invalid or expired enrollment code")
    api_key = new_api_key()
    pc.api_key_hash = hash_secret(api_key)
    pc.registered_at = now
    pc.last_seen_at = now
    record(db, f"pc:{pc.id}", "pc_registered", {})
    db.commit()
    settings = request.app.state.settings
    return {"pc_id": pc.id, "api_key": api_key,
            "poll_interval_seconds": get_poll_interval(
                db, settings.default_poll_interval_seconds)}


@router.post("/{pc_id}/heartbeat", status_code=204)
def heartbeat(pc: Pc = Depends(require_pc), db: Session = Depends(get_db)):
    """Phase 1 stub: only refreshes last_seen_at (richer payload is Phase 2)."""
    pc.last_seen_at = utcnow()
    db.commit()
    return Response(status_code=204)


@router.get("/{pc_id}/config")
def get_config(request: Request, pc: Pc = Depends(require_pc),
               db: Session = Depends(get_db),
               x_operator_id: Optional[str] = Header(default=None)):
    """The sync client polls this. X-Operator-Id is audit-only: the operator
    does not authenticate to the server in Phase 1."""
    projects = list(db.scalars(
        select(Project).join(PcAssignment,
                             PcAssignment.project_id == Project.id)
        .where(PcAssignment.pc_id == pc.id).order_by(Project.name, Project.id)))
    pc.last_seen_at = utcnow()
    record(db, f"pc:{pc.id}", "config_pull",
           {"operator": sanitize_log_value(x_operator_id),
            "projects": {p.id: p.current_version for p in projects}})
    db.commit()
    default = request.app.state.settings.default_poll_interval_seconds
    return {"pc_id": pc.id,
            "poll_interval_seconds": get_poll_interval(db, default),
            "projects": [{"project_id": p.id, "name": p.name,
                          "version": p.current_version,
                          "config": json.loads(p.config_json)}
                         for p in projects]}
