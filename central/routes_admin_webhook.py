"""Admin: the alert webhook (set it up, send a test). The address and secret are stored encrypted and are never
returned: the dashboard sees only the host name and whether a secret is set."""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from central import notify
from central.audit import record
from central.deps import get_db, require_admin, require_csrf
from central.models import AdminUser

router = APIRouter(prefix="/admin", tags=["admin-webhook"],
                   dependencies=[Depends(require_admin), Depends(require_csrf)])

NO_KEY = ("Set CENTRAL_DATA_KEY on the server first (generate one with: python -m central.manage generate-key; "
          "see central/deploy/DEPLOY.md). The webhook address is stored encrypted and needs it.")


class WebhookIn(BaseModel):
    url: Optional[str] = Field(default=None, max_length=notify.MAX_URL)         # None: leave as it is; "": remove
    secret: Optional[str] = Field(default=None, max_length=200)                 # None: leave as it is; "": remove
    enabled: bool = False
    min_level: Literal["info", "warn", "critical"] = "warn"


def _key(request: Request):
    return request.app.state.settings.data_key


@router.get("/webhook")
def read_webhook(request: Request, db: Session = Depends(get_db)):
    return notify.public_status(db, _key(request))


@router.put("/webhook")
def save_webhook(body: WebhookIn, request: Request, admin: AdminUser = Depends(require_admin),
                 db: Session = Depends(get_db)):
    key, settings = _key(request), request.app.state.settings
    if key is None:
        raise HTTPException(409, NO_KEY)
    url = body.url.strip() if body.url is not None else None
    existing = notify.load_config(db, key)
    try:
        if url:
            host = notify.check_url(url, settings.webhook_allow_http, settings.webhook_block_private)
        else:
            host = None
        clearing = url == ""                       # removing the address also switches the webhook off
        will_have_address = bool(url) or (url is None and existing is not None)
        if body.enabled and not clearing and not will_have_address:
            raise notify.WebhookConfigError("enter the webhook address before switching it on")
        notify.save_config(db, key, url=url, secret=(body.secret or "").strip() or None, enabled=body.enabled,
                           min_level=body.min_level, change_secret=body.secret is not None)
    except notify.WebhookConfigError as e:
        db.rollback()
        raise HTTPException(422, str(e)) from e
    status = notify.public_status(db, key)
    record(db, f"admin:{admin.username}", "webhook_configured",
           {"host": host or status["host"], "enabled": status["enabled"], "min_level": body.min_level,
            "secret": "set" if status["has_secret"] else "none"})
    db.commit()
    return notify.public_status(db, key)


@router.post("/webhook/test")
def test_webhook(request: Request, admin: AdminUser = Depends(require_admin), db: Session = Depends(get_db)):
    key = _key(request)
    if key is None:
        raise HTTPException(409, NO_KEY)
    notifier = request.app.state.notifier
    result = notifier.send_test(db)
    record(db, f"admin:{admin.username}", "webhook_tested",
           {"ok": result["ok"], "status": result["status"], "error": result["error"]})
    db.commit()
    return result
