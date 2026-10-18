"""Admin: fleet monitoring (read-only). Which PCs are working, are they running the current
project settings, and how much are they processing. Everything here comes from what the PCs
report with their heartbeat: versions and counts, never document content."""
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from central.deps import get_db, require_admin, require_csrf
from central.models import (ConfigVersion, Pc, PcAppliedProject, PcAssignment, PcDailyStat,
                            Project, utcnow)
from central.routes_admin_overview import _iso, is_online, online_window

router = APIRouter(prefix="/admin", tags=["admin-fleet"],
                   dependencies=[Depends(require_admin), Depends(require_csrf)])

COUNTS = ("documents", "pages", "batches", "errors")
MAX_DAYS = 90
STATION_DAYS = 30


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


@router.get("/alerts")
def alerts(request: Request, db: Session = Depends(get_db)):
    """What needs attention right now, most serious first (see central/alerts.py for the rules).
    Computed on request: an alert exists exactly while its condition does."""
    from central.alerts import compute_alerts
    found = compute_alerts(db, online_window(db, request))
    counts = {"critical": 0, "warn": 0, "info": 0}
    for a in found:
        counts[a["severity"]] += 1
    return {"alerts": found, "counts": counts}


@router.get("/fleet/{pc_id}")
def station(pc_id: str, request: Request, db: Session = Depends(get_db)):
    """Everything about one station: status, the settings history of each project it is assigned against what it
    runs, 30 days of activity, its own alerts and its recent events (not the routine configuration polls)."""
    from sqlalchemy import or_

    from central.alerts import compute_alerts
    from central.models import AuditEvent
    from central.routes_admin_overview import _event
    pc = db.get(Pc, pc_id)
    if pc is None:
        raise HTTPException(404, "unknown PC")
    window = online_window(db, request)
    assigned = list(db.scalars(select(Project).join(PcAssignment, PcAssignment.project_id == Project.id)
                               .where(PcAssignment.pc_id == pc.id).order_by(Project.name, Project.id)))
    have = {r.project_id: r.version for r in db.scalars(select(PcAppliedProject).where(PcAppliedProject.pc_id == pc.id))}
    rollout, rows = _rollout(pc, assigned, have)
    for row in rows:
        versions = list(db.scalars(select(ConfigVersion).where(ConfigVersion.kind == "project",
                                                               ConfigVersion.owner_id == row["project_id"])
                                   .order_by(ConfigVersion.version.desc()).limit(5)))
        row["history"] = [{"version": v.version, "published_at": _iso(v.created_at), "published_by": v.created_by}
                          for v in versions]
        row["published_at"] = next((h["published_at"] for h in row["history"]
                                    if h["version"] == row["assigned_version"]), None)

    end = utcnow().date()
    wanted = [(end - timedelta(days=i)).isoformat() for i in range(STATION_DAYS - 1, -1, -1)]
    per_day = {d: _zero() for d in wanted}
    for r in db.scalars(select(PcDailyStat).where(PcDailyStat.pc_id == pc.id, PcDailyStat.day >= wanted[0])):
        if r.day in per_day:
            per_day[r.day] = _counts(r)
    totals = _zero()
    for counts in per_day.values():
        for k, v in counts.items():
            totals[k] += v
    today = per_day[wanted[-1]]

    events = db.scalars(select(AuditEvent).where(or_(
        (AuditEvent.actor == f"pc:{pc.id}") & (AuditEvent.action != "config_pull"),
        AuditEvent.detail.contains(pc.id))).order_by(AuditEvent.id.desc()).limit(25))
    return {
        "pc": {"id": pc.id, "display_name": pc.display_name, "status": _status(pc, window),
               "app_version": pc.app_version, "last_seen_at": _iso(pc.last_seen_at), "reported_at": _iso(pc.reported_at),
               "registered_at": _iso(pc.registered_at), "created_at": _iso(pc.created_at)},
        "rollout": rollout, "projects": rows,
        "days": [{"day": d, **per_day[d]} for d in wanted], "totals": totals,
        "today": today if any(today.values()) else None,
        "alerts": [a for a in compute_alerts(db, window) if a["pc_id"] == pc.id],
        "events": [_event(e) for e in events]}
