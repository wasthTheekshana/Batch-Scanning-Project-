"""What a PC tells the central service with each sync, so the admin web center can show the fleet.

Only versions and counts: the application version, the settings version of each centrally managed
project the PC runs, and today's totals (documents, pages, batches, errors). Never document text,
names, index values, file names or paths."""
from __future__ import annotations

from datetime import datetime, timezone


def build_report(db, now: datetime | None = None) -> dict:
    from version import __version__
    now = now or datetime.now(timezone.utc)
    day = now.date().isoformat()
    applied = {r["project_id"]: int(r["central_version"])
               for r in db.list_central_projects(managed_only=True)}
    return {"app_version": __version__, "applied": applied,
            "stats": {"day": day, **db.activity_for_day(day)}}
