"""Admin-configurable global settings stored in the settings table."""
from sqlalchemy.orm import Session

from central.models import Setting

_POLL_KEY = "poll_interval_seconds"


def get_poll_interval(db: Session, default: int) -> int:
    row = db.get(Setting, _POLL_KEY)
    return int(row.value) if row else default


def set_poll_interval(db: Session, seconds: int) -> None:
    row = db.get(Setting, _POLL_KEY)
    if row:
        row.value = str(seconds)
    else:
        db.add(Setting(key=_POLL_KEY, value=str(seconds)))
