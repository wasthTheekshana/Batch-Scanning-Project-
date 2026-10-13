"""Admin: the audit log and the dashboard overview (read-only)."""
import json
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from central.deps import get_db, require_admin, require_csrf
from central.models import (AdminUser, AuditEvent, Pc, Project, Template,
                            utcnow)
from central.routes_admin_users import DISABLED_UNTIL
from central.settings_store import get_poll_interval

router = APIRouter(prefix="/admin", tags=["admin-overview"],
                   dependencies=[Depends(require_admin),
                                 Depends(require_csrf)])

Name = Annotated[str | None, Query(pattern=r"^[A-Za-z0-9_:.@-]{1,150}$")]


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def online_window(db: Session, request: Request) -> timedelta:
    """A PC is online if it checked in within three polling intervals (at
    least five minutes), so one missed poll does not flag it."""
    poll = get_poll_interval(
        db, request.app.state.settings.default_poll_interval_seconds)
    return timedelta(seconds=max(3 * poll, 300))


def is_online(pc: Pc, window: timedelta) -> bool:
    return bool(pc.last_seen_at and utcnow() - pc.last_seen_at <= window)


def _event(e: AuditEvent) -> dict:
    try:
        detail = json.loads(e.detail or "{}")
        if not isinstance(detail, dict):
            detail = {}
    except ValueError:
        detail = {}
    return {"id": e.id, "at": _iso(e.at), "actor": e.actor,
            "action": e.action, "detail": detail}


@router.get("/audit")
def read_audit(limit: Annotated[int, Query(ge=1, le=200)] = 50,
               before_id: Annotated[int | None, Query(ge=1)] = None,
               action: Name = None, actor: Name = None,
               db: Session = Depends(get_db)):
    query = select(AuditEvent).order_by(AuditEvent.id.desc())
    if before_id is not None:
        query = query.where(AuditEvent.id < before_id)
    if action:
        query = query.where(AuditEvent.action == action)
    if actor:
        query = query.where(AuditEvent.actor == actor)
    rows = list(db.scalars(query.limit(limit + 1)))
    more = len(rows) > limit
    rows = rows[:limit]
    return {"events": [_event(e) for e in rows],
            "next_before_id": rows[-1].id if more else None}


@router.get("/audit/actions")
def audit_actions(db: Session = Depends(get_db)):
    return sorted(db.scalars(select(AuditEvent.action).distinct()))


@router.get("/overview")
def overview(request: Request, db: Session = Depends(get_db)):
    window = online_window(db, request)
    pcs = list(db.scalars(select(Pc).order_by(Pc.display_name)))
    registered = [p for p in pcs if p.api_key_hash is not None]
    online = [p for p in registered if is_online(p, window)]
    admins = list(db.scalars(select(AdminUser)))
    recent = db.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(10))
    return {
        "pcs": {"total": len(pcs), "registered": len(registered),
                "online": len(online),
                "offline": [p.display_name for p in registered
                            if not is_online(p, window)],
                "online_window_seconds": int(window.total_seconds())},
        "projects": db.scalar(select(func.count()).select_from(Project)),
        "templates": db.scalar(select(func.count()).select_from(Template)),
        "admins": {"total": len(admins),
                   "active": sum(1 for a in admins
                                 if not (a.locked_until
                                         and a.locked_until >= DISABLED_UNTIL))},
        "poll_interval_seconds": get_poll_interval(
            db, request.app.state.settings.default_poll_interval_seconds),
        "recent_audit": [_event(e) for e in recent],
    }
