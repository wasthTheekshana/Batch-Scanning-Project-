"""What needs an administrator's attention, how serious it is, and for how long.

Computed from what the PCs report and from the server's own records, not stored: an alert
exists exactly while its condition does. A station still inside the grace window (the online
window: three polling intervals, at least five minutes) raises nothing, so one missed poll is
not an incident.

| kind          | when                                                   | severity                       |
|---------------|--------------------------------------------------------|--------------------------------|
| offline       | silent beyond the grace window                         | warn, critical after a day     |
| behind        | a published settings version not applied beyond grace  | warn, critical after a day     |
| errors        | errors reported today                                  | info, warn from ERRORS_WARN    |
| revoked       | the station's key was revoked                          | info                           |
| not_enrolled  | added but never connected for NEVER_CONNECTED_AFTER    | info                           |
| backlog       | documents waiting to be reported for BACKLOG_AFTER     | warn, critical after a day     |
| refused       | the server refused documents the station reported      | warn                           |
"""
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from central.models import (ConfigVersion, Pc, PcAppliedProject, PcAssignment, PcDailyStat,
                            Project, utcnow)

CRITICAL_AFTER = timedelta(hours=24)
NEVER_CONNECTED_AFTER = timedelta(days=7)
ERRORS_WARN = 5
BACKLOG_AFTER = timedelta(hours=4)
ORDER = {"critical": 0, "warn": 1, "info": 2}


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def _severity(age: timedelta) -> str:
    return "critical" if age >= CRITICAL_AFTER else "warn"


def compute_alerts(db: Session, window: timedelta) -> list[dict]:
    now = utcnow()
    today = now.date().isoformat()
    pcs = list(db.scalars(select(Pc).order_by(Pc.display_name)))
    projects = {p.id: p for p in db.scalars(select(Project))}
    assigned: dict[str, list[Project]] = {}
    for a in db.scalars(select(PcAssignment)):
        if a.project_id in projects:
            assigned.setdefault(a.pc_id, []).append(projects[a.project_id])
    applied: dict[str, dict[str, int]] = {}
    for r in db.scalars(select(PcAppliedProject)):
        applied.setdefault(r.pc_id, {})[r.project_id] = r.version
    errors = {r.pc_id: r.errors for r in db.scalars(select(PcDailyStat).where(PcDailyStat.day == today))}
    published = {(c.owner_id, c.version): c.created_at for c in db.scalars(
        select(ConfigVersion).where(ConfigVersion.kind == "project"))}

    out: list[dict] = []

    def add(kind, severity, pc, message, since, project_id=None):
        out.append({"id": f"{kind}:{pc.id}" + (f":{project_id}" if project_id else ""), "kind": kind,
                    "severity": severity, "pc_id": pc.id, "pc_name": pc.display_name,
                    "message": message, "since": _iso(since)})

    for pc in pcs:
        if pc.api_key_hash is None:
            if pc.registered_at:
                add("revoked", "info", pc, "Access revoked: it no longer receives settings", pc.last_seen_at)
            elif now - pc.created_at >= NEVER_CONNECTED_AFTER:
                add("not_enrolled", "info", pc, "Added but never connected", pc.created_at)
            continue
        silent = now - pc.last_seen_at if pc.last_seen_at else None
        if silent is None or silent > window:
            add("offline", _severity(silent or CRITICAL_AFTER), pc, "Stopped checking in", pc.last_seen_at)
        elif pc.reported_at is not None:               # a station that never reports cannot be called behind
            have = applied.get(pc.id, {})
            for project in sorted(assigned.get(pc.id, []), key=lambda p: (p.name, p.id)):
                version = have.get(project.id)
                if version is not None and version >= project.current_version:
                    continue
                since = published.get((project.id, project.current_version))
                age = now - since if since else None
                if age is not None and age <= window:
                    continue                           # published a moment ago: it has not had its chance yet
                note = "not applied yet" if version is None else f"it runs v{version}"
                add("behind", _severity(age or CRITICAL_AFTER), pc,
                    f"Has not applied {project.name} v{project.current_version} ({note})", since, project.id)
        if pc.docs_waiting and pc.docs_oldest_at and now - pc.docs_oldest_at >= BACKLOG_AFTER:
            add("backlog", _severity(now - pc.docs_oldest_at), pc,
                f"{pc.docs_waiting} {'document is' if pc.docs_waiting == 1 else 'documents are'} waiting to reach "
                "the server", pc.docs_oldest_at)
        if pc.docs_refused:
            add("refused", "warn", pc,
                f"{pc.docs_refused} {'document was' if pc.docs_refused == 1 else 'documents were'} refused by the "
                "server (check that the project is assigned to this station)", None)
        n = errors.get(pc.id, 0)
        if n:
            add("errors", "warn" if n >= ERRORS_WARN else "info", pc,
                f"{n} {'error' if n == 1 else 'errors'} today", None)

    out.sort(key=lambda a: (ORDER[a["severity"]], a["since"] or "9", a["pc_name"]))
    return out
