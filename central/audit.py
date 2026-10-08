"""Server-side audit trail (adds to the session; the caller commits)."""
import json

from central.models import AuditEvent


def record(db, actor: str, action: str, detail: dict | None = None) -> None:
    db.add(AuditEvent(actor=actor, action=action,
                      detail=json.dumps(detail or {})))
