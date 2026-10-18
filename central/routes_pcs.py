"""PC-facing endpoints: registration, heartbeat, config pull."""
import json
import re
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from central.audit import record
from central.deps import get_db, require_pc
from central.models import (EnrollmentCode, Pc, PcAppliedProject, PcAssignment, PcDailyStat,
                            Project, utcnow)
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


MAX_COUNT = 10_000_000          # a day's documents or pages on one PC: far above any real figure
MAX_APPLIED = 200               # projects a PC can report
KEEP_DAYS = 400                 # daily rows older than this are deleted
VERSION = re.compile(r"^[0-9A-Za-z.+-]{1,32}$")
PROJECT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class StatsIn(BaseModel):
    """One PC's running totals for one UTC day. Counts only, never document content."""
    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    documents: int = Field(ge=0, le=MAX_COUNT)
    pages: int = Field(ge=0, le=MAX_COUNT)
    batches: int = Field(ge=0, le=MAX_COUNT)
    errors: int = Field(ge=0, le=MAX_COUNT)

    @field_validator("day")
    @classmethod
    def _plausible_day(cls, value: str) -> str:
        try:
            d = date.fromisoformat(value)
        except ValueError:
            raise ValueError("not a date") from None
        today = utcnow().date()
        if not today - timedelta(days=2) <= d <= today + timedelta(days=1):
            raise ValueError("not a recent day")      # a PC clock this far off is junk, not data
        return value


class HeartbeatIn(BaseModel):
    app_version: Optional[str] = Field(default=None, max_length=32)
    applied: Optional[dict[str, int]] = None          # project id -> the settings version it runs
    stats: Optional[StatsIn] = None

    @field_validator("app_version")
    @classmethod
    def _version(cls, value):
        if value is not None and not VERSION.match(value):
            raise ValueError("not a version")
        return value

    @field_validator("applied")
    @classmethod
    def _applied(cls, value):
        if value is None:
            return value
        if len(value) > MAX_APPLIED:
            raise ValueError("too many projects")
        for key, version in value.items():
            if not PROJECT_ID.match(key) or not 0 <= version <= 10 ** 9:
                raise ValueError("bad project entry")
        return value


@router.post("/{pc_id}/heartbeat", status_code=204)
def heartbeat(body: Optional[HeartbeatIn] = None, pc: Pc = Depends(require_pc),
              db: Session = Depends(get_db)):
    """Refreshes last_seen_at. A PC that sends a body also reports its app version, which
    project settings versions it runs, and its running totals for the day (the Fleet and
    Activity views). It is not written to the audit log: one row per PC poll would bury it."""
    now = utcnow()
    pc.last_seen_at = now
    if body is not None:
        pc.reported_at = now
        if body.app_version is not None:
            pc.app_version = body.app_version
        if body.applied is not None:
            db.execute(delete(PcAppliedProject).where(PcAppliedProject.pc_id == pc.id))
            db.add_all(PcAppliedProject(pc_id=pc.id, project_id=pid, version=v, reported_at=now)
                       for pid, v in body.applied.items())
        if body.stats is not None:
            row = db.get(PcDailyStat, (pc.id, body.stats.day))
            if row is None:
                row = PcDailyStat(pc_id=pc.id, day=body.stats.day)
                db.add(row)
            row.documents, row.pages = body.stats.documents, body.stats.pages
            row.batches, row.errors = body.stats.batches, body.stats.errors
            cutoff = (now.date() - timedelta(days=KEEP_DAYS)).isoformat()
            db.execute(delete(PcDailyStat).where(PcDailyStat.day < cutoff))
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
