"""Admin: fleet monitoring (read-only). Which PCs are working, are they running the current
project settings, and how much are they processing. Everything here comes from what the PCs
report with their heartbeat: versions and counts, never document content."""
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from central.deps import get_db, require_admin, require_csrf
from central.models import (Pc, PcAppliedProject, PcAssignment, PcDailyStat, Project,
                            utcnow)
from central.routes_admin_overview import _iso, is_online, online_window

router = APIRouter(prefix="/admin", tags=["admin-fleet"],
                   dependencies=[Depends(require_admin), Depends(require_csrf)])

COUNTS = ("documents", "pages", "batches", "errors")
MAX_DAYS = 90


def _status(pc: Pc, window: timedelta) -> str:
    if pc.api_key_hash is None:
        return "key_revoked" if pc.registered_at else "not_registered"
    return "online" if is_online(pc, window) else "offline"


def _zero() -> dict[str, int]:
    return {k: 0 for k in COUNTS}


def _counts(row) -> dict[str, int]:
    return {k: getattr(row, k) for k in COUNTS}


def _rollout(pc: Pc, assigned: list[Project], applied: dict[str, int]) -> tuple[str, list[dict]]:
    """Per project: the version assigned here, the one the PC reports, and where it stands. A PC
    that never reported (an older program) is "unknown", not "behind": no false alarm."""
    rows = []
    for p in assigned:
        have = applied.get(p.id)
        if pc.reported_at is None:
            state = "unknown"
        elif have is None:
            state = "not_applied"
        elif have < p.current_version:
            state = "behind"
        else:
            state = "current"
        rows.append({"project_id": p.id, "name": p.name, "assigned_version": p.current_version,
                     "applied_version": have, "state": state})
    states = {r["state"] for r in rows}
    if not rows:
        return "none", rows
    if states & {"behind", "not_applied"}:
        return "behind", rows
    return ("unknown" if "unknown" in states else "current"), rows


@router.get("/fleet")
def fleet(request: Request, db: Session = Depends(get_db)):
    window = online_window(db, request)
    today = utcnow().date().isoformat()
    pcs = list(db.scalars(select(Pc).order_by(Pc.display_name)))
    projects = {p.id: p for p in db.scalars(select(Project))}
    assigned: dict[str, list[Project]] = {}
    for a in db.scalars(select(PcAssignment)):
        if a.project_id in projects:
            assigned.setdefault(a.pc_id, []).append(projects[a.project_id])
    applied: dict[str, dict[str, int]] = {}
    for r in db.scalars(select(PcAppliedProject)):
        applied.setdefault(r.pc_id, {})[r.project_id] = r.version
    todays = {r.pc_id: r for r in db.scalars(select(PcDailyStat).where(PcDailyStat.day == today))}

    out, summary_today = [], _zero()
    summary = {"total": len(pcs), "online": 0, "offline": 0, "not_registered": 0,
               "key_revoked": 0, "behind": 0, "versions": {}}
    for pc in pcs:
        status = _status(pc, window)
        summary[status] += 1
        rollout, project_rows = _rollout(
            pc, sorted(assigned.get(pc.id, []), key=lambda p: (p.name, p.id)), applied.get(pc.id, {}))
        if status in ("online", "offline") and rollout == "behind":
            summary["behind"] += 1
        version = pc.app_version or "unknown"
        summary["versions"][version] = summary["versions"].get(version, 0) + 1
        row = todays.get(pc.id)
        if row is not None:
            for k, v in _counts(row).items():
                summary_today[k] += v
        out.append({"id": pc.id, "display_name": pc.display_name, "status": status,
                    "last_seen_at": _iso(pc.last_seen_at), "reported_at": _iso(pc.reported_at),
                    "app_version": pc.app_version, "rollout": rollout, "projects": project_rows,
                    "today": _counts(row) if row is not None else None})
    summary["today"] = summary_today
    return {"summary": summary, "pcs": out}


@router.get("/stats")
def stats(days: Annotated[int, Query(ge=1, le=MAX_DAYS)] = 14, db: Session = Depends(get_db)):
    """Documents, pages, batches and errors per day for the last `days` days (today included),
    every day present (zeros when quiet), plus each PC's total over the window, busiest first."""
    end = utcnow().date()
    wanted = [(end - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    rows = list(db.scalars(select(PcDailyStat).where(PcDailyStat.day >= wanted[0],
                                                     PcDailyStat.day <= wanted[-1])))
    per_day = {d: _zero() for d in wanted}
    per_pc: dict[str, dict[str, int]] = {}
    for r in rows:
        for k, v in _counts(r).items():
            per_day[r.day][k] += v
            per_pc.setdefault(r.pc_id, _zero())[k] += v
    names = {p.id: p.display_name for p in db.scalars(select(Pc))}
    totals = _zero()
    for counts in per_day.values():
        for k, v in counts.items():
            totals[k] += v
    ranked = sorted(per_pc.items(), key=lambda kv: (-kv[1]["documents"], names.get(kv[0], "")))
    return {"days": [{"day": d, **per_day[d]} for d in wanted], "totals": totals,
            "per_pc": [{"pc_id": pid, "display_name": names.get(pid, "(deleted)"), **c}
                       for pid, c in ranked]}
