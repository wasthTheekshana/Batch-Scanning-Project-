"""Alerts delivered by webhook.

An administrator sets one webhook address (and, optionally, a signing secret and the lowest level worth a message).
A background worker compares the alerts that exist now (central/alerts.py) with the ones it has already announced
(the `alert_state` table) and sends an event for each change:

    alert.opened     a new alert (at or above the chosen level)
    alert.escalated  the same alert became more serious (warning -> critical)
    alert.reminder   a critical alert still open 12 hours after the last message
    alert.resolved   an alert it announced no longer exists
    test             sent by "Send a test message"

The body is JSON with a ready-to-read `text` (Slack, Teams and similar services show it as it is) plus the alert
in full. When a secret is set, `X-DOK-Signature: sha256=<hmac of "<timestamp>.<body>">` lets the receiver check the
message is ours and fresh. The address is stored encrypted (it often contains a secret key) and never shown again.

Several server processes can run this worker: each event is first CLAIMED in the database (a unique row, or a
compare-and-swap update), so it is sent once; a failed send releases the claim and the next cycle tries again.
Safety: https only (http is a development switch), no redirects, a short timeout, the response is ignored, and an
address that resolves to this machine or a link-local address (cloud metadata) is refused.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlparse

from typing import cast

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from central.alerts import ORDER, compute_alerts
from central.audit import record
from central.crypto import DataKey, DecryptionError
from central.models import AlertState, Setting, utcnow
from central.settings_store import get_poll_interval

log = logging.getLogger("dokscanner.central")

REMIND_CRITICAL = timedelta(hours=12)
TIMEOUT_SECONDS = 10
MAX_URL = 2000
LEVELS = ("info", "warn", "critical")
LEVEL_TEXT = {"critical": "Critical", "warn": "Warning", "info": "Note"}

URL_KEY, SECRET_KEY = "webhook.url", "webhook.secret"
ENABLED_KEY, LEVEL_KEY, RESULT_KEY = "webhook.enabled", "webhook.min_level", "webhook.last_result"


class WebhookConfigError(ValueError):
    """The address or setting cannot be accepted; the message is safe to show (it never quotes the address)."""


class WebhookSendError(Exception):
    """A send failed; the message is safe to show and to store (no address, no secret)."""


# ---- the address -------------------------------------------------------------------------------------------
def _resolve(host: str, port: int) -> list[str]:
    """Every address the host name points to (a module-level function so tests can stand in for DNS)."""
    try:
        return sorted({str(info[4][0]) for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    except socket.gaierror as e:
        raise WebhookConfigError("the address does not resolve to a server") from e


def check_url(url: str, allow_http: bool = False, block_private: bool = False) -> str:
    """Accept a webhook address or explain why not. Returns the host name (the only part shown later)."""
    if not url or len(url) > MAX_URL or any(c in url for c in "\r\n\t "):
        raise WebhookConfigError("enter the address as one line, up to 2000 characters")
    parts = urlparse(url)
    if parts.scheme != "https" and not (allow_http and parts.scheme == "http"):
        raise WebhookConfigError("the address must start with https://")
    if not parts.hostname or parts.username or parts.password:
        raise WebhookConfigError("the address needs a host name and no user name or password in it")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as e:
        raise WebhookConfigError("the port in the address is not valid") from e
    for text in _resolve(parts.hostname, port):
        ip = ipaddress.ip_address(text)
        local = ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved
        if (local and not allow_http) or (block_private and ip.is_private and not ip.is_loopback):
            raise WebhookConfigError("that address points inside this server's own network position, which is not allowed")
    return parts.hostname


# ---- the message -------------------------------------------------------------------------------------------
def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds") + "Z"


def event_payload(event: str, alert: dict | None, now: datetime) -> dict:
    body: dict[str, object] = {"event": event, "at": iso(now)}
    if event == "test" or alert is None:
        text = "DOK Scanner central: this is a test message. Alert notifications are working."
    elif event == "alert.resolved":
        text = f"Resolved: {alert['pc_name']}: {alert['message']}"
    else:
        label = LEVEL_TEXT[alert["severity"]]
        text = f"[{label}] {alert['pc_name']}: {alert['message']}" + (
            " (reminder, still open)" if event == "alert.reminder" else "")
    body["text"] = text
    if alert is not None and event != "test":
        body["alert"] = alert
    return body


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), timestamp.encode("ascii") + b"." + body,
                                hashlib.sha256).hexdigest()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def http_post(url: str, body: bytes, headers: dict, timeout: float = TIMEOUT_SECONDS) -> int:
    """POST and return the status code. Errors become WebhookSendError with a safe message."""
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout) as response:
            response.read(1024)
            return response.status
    except urllib.error.HTTPError as e:
        e.close()
        return e.code
    except (TimeoutError, socket.timeout) as e:
        raise WebhookSendError("timed out") from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise WebhookSendError("could not connect") from e


# ---- the stored settings ----------------------------------------------------------------------------------------
@dataclass
class WebhookConfig:
    url: str
    secret: str | None
    enabled: bool
    min_level: str


def _get(db: Session, key: str) -> str | None:
    row = db.get(Setting, key)
    return row.value if row else None


def _put(db: Session, key: str, value: str | None) -> None:
    row = db.get(Setting, key)
    if value is None:
        if row:
            db.delete(row)
    elif row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))


def save_config(db: Session, data_key: DataKey, *, url: str | None, secret: str | None, enabled: bool,
                min_level: str, change_secret: bool) -> None:
    """Store the settings (the address and secret encrypted). url None/"" clears the webhook."""
    if min_level not in LEVELS:
        raise WebhookConfigError("the lowest level must be note, warning or critical")
    if url:
        _put(db, URL_KEY, data_key.encrypt(url, URL_KEY))
    elif url is not None:
        _put(db, URL_KEY, None)
        _put(db, SECRET_KEY, None)
        enabled = False
    if change_secret:
        _put(db, SECRET_KEY, data_key.encrypt(secret, SECRET_KEY) if secret else None)
    _put(db, ENABLED_KEY, "1" if enabled else "0")
    _put(db, LEVEL_KEY, min_level)
    if not enabled:                              # nothing is announced while it is off: start clean when it is back on
        db.execute(delete(AlertState))


def load_config(db: Session, data_key: DataKey | None) -> WebhookConfig | None:
    stored = _get(db, URL_KEY)
    if not stored or data_key is None:
        return None
    try:
        url = data_key.decrypt_text(stored, URL_KEY)
        secret_stored = _get(db, SECRET_KEY)
        secret = data_key.decrypt_text(secret_stored, SECRET_KEY) if secret_stored else None
    except DecryptionError:
        return None
    return WebhookConfig(url, secret, _get(db, ENABLED_KEY) == "1", _get(db, LEVEL_KEY) or "warn")


def public_status(db: Session, data_key: DataKey | None) -> dict:
    """What the dashboard may show: never the address (only its host) and never the secret."""
    config = load_config(db, data_key)
    last = _get(db, RESULT_KEY)
    return {"key_available": data_key is not None,
            "configured": config is not None,
            "enabled": bool(config and config.enabled),
            "host": urlparse(config.url).hostname if config else None,
            "has_secret": bool(config and config.secret),
            "min_level": config.min_level if config else (_get(db, LEVEL_KEY) or "warn"),
            "last_result": json.loads(last) if last else None}


def _remember(db: Session, ok: bool, status: int | None, error: str | None, event: str) -> None:
    _put(db, RESULT_KEY, json.dumps({"at": iso(utcnow()), "ok": ok, "status": status, "error": error, "event": event}))


# ---- sending ---------------------------------------------------------------------------------------------------
def deliver(config: WebhookConfig, payload: dict, transport=http_post, now: datetime | None = None) -> int:
    """Send one message; return the receiver's status. Raises WebhookSendError. A non-2xx status is a failure too."""
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    stamp = str(int((now or utcnow()).timestamp()))
    headers = {"Content-Type": "application/json", "User-Agent": "dokscanner-central",
               "X-DOK-Event": payload["event"], "X-DOK-Timestamp": stamp}
    if config.secret:
        headers["X-DOK-Signature"] = sign(config.secret, stamp, body)
    status = transport(config.url, body, headers)
    if not 200 <= status < 300:
        raise WebhookSendError(f"the receiver answered {status}")
    return status


def plan(alerts: list[dict], state: dict[str, AlertState], min_level: str, now: datetime
         ) -> tuple[list[tuple[str, dict, AlertState | None]], list[str]]:
    """(the events to send as (name, alert, the state row it concerns), the announced alerts to forget silently:
    still present but below a level that was raised). Pure, so it can be tested without a database."""
    floor = ORDER[min_level]
    events: list[tuple[str, dict, AlertState | None]] = []
    eligible: dict[str, dict] = {a["id"]: a for a in alerts if ORDER[a["severity"]] <= floor}
    present = {a["id"] for a in alerts}
    for alert_id, alert in eligible.items():
        row = state.get(alert_id)
        if row is None:
            events.append(("alert.opened", alert, None))
        elif ORDER[alert["severity"]] < ORDER[row.severity]:
            events.append(("alert.escalated", alert, row))
        elif alert["severity"] == "critical" and now - row.last_sent_at >= REMIND_CRITICAL:
            events.append(("alert.reminder", alert, row))
    forget: list[str] = []
    for alert_id, row in state.items():
        if alert_id in eligible:
            continue
        if alert_id in present:
            forget.append(alert_id)                      # still there but below the new level: no message
            continue
        parts = alert_id.split(":")
        gone = {"id": alert_id, "severity": row.severity, "kind": parts[0], "pc_id": parts[1] if len(parts) > 1 else "",
                "pc_name": row.pc_name, "message": row.message, "since": None}
        events.append(("alert.resolved", gone, row))
    return events, forget


class Notifier:
    def __init__(self, session_factory, data_key: DataKey | None, *, allow_http=False, block_private=False,
                 interval: float = 60.0, transport=http_post, default_poll: int = 600):
        self.session_factory, self.data_key = session_factory, data_key
        self.default_poll = default_poll
        self.allow_http, self.block_private = allow_http, block_private
        self.interval, self.transport = interval, transport
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- one pass --------------------------------------------------------------------------------------------------
    def run_once(self, window: timedelta | None = None) -> dict:
        """Announce what changed. Returns {"sent": n, "failed": n, "skipped": reason or None}."""
        result: dict[str, int | str | None] = {"sent": 0, "failed": 0, "skipped": None}
        with self.session_factory() as db:
            config = load_config(db, self.data_key)
            if config is None:
                result["skipped"] = "not_configured"
                return result
            if not config.enabled:
                result["skipped"] = "disabled"
                return result
            if window is None:                           # the same grace window the dashboard uses
                window = timedelta(seconds=max(3 * get_poll_interval(db, self.default_poll), 300))
            now = utcnow()
            alerts = compute_alerts(db, window)
            state = {r.alert_id: r for r in db.scalars(select(AlertState))}
            events, forget = plan(alerts, state, config.min_level, now)
            if forget:
                db.execute(delete(AlertState).where(AlertState.alert_id.in_(forget)))
                db.commit()
            for event, alert, row in events:
                self._announce(db, config, event, alert, row, now, result)
        return result

    def _announce(self, db: Session, config: WebhookConfig, event: str, alert: dict, row: AlertState | None,
                  now: datetime, result: dict) -> None:
        sent_count = result["sent"]
        claimed = self._claim(db, event, alert, row, now)
        if not claimed:
            return                                       # another process has taken it
        try:
            status = deliver(config, event_payload(event, alert, now), self.transport, now)
        except WebhookSendError as e:
            self._release(db, event, alert, row)
            _remember(db, False, None, str(e), event)
            record(db, "system:alerts", "webhook_failed", {"event": event, "alert": alert["id"], "error": str(e)})
            db.commit()
            result["failed"] = int(result["failed"]) + 1
            return
        _remember(db, True, status, None, event)
        record(db, "system:alerts", "webhook_sent", {"event": event, "alert": alert["id"], "status": status})
        db.commit()
        result["sent"] = int(sent_count) + 1

    # -- claims: exactly one process announces each change -------------------------------------------------------
    @staticmethod
    def _claim(db: Session, event: str, alert: dict, row: AlertState | None, now: datetime) -> bool:
        if event == "alert.opened":
            db.add(AlertState(alert_id=alert["id"], severity=alert["severity"], pc_name=alert["pc_name"],
                              message=alert["message"], opened_at=now, last_sent_at=now))
            try:
                db.commit()
                return True
            except IntegrityError:
                db.rollback()
                return False
        assert row is not None                           # every other event concerns an announced alert
        res: CursorResult
        if event == "alert.resolved":
            res = cast(CursorResult, db.execute(delete(AlertState).where(
                AlertState.alert_id == alert["id"], AlertState.last_sent_at == row.last_sent_at)))
        else:                                            # escalated / reminder: swap only if nobody else did
            res = cast(CursorResult, db.execute(update(AlertState).where(
                AlertState.alert_id == alert["id"], AlertState.severity == row.severity,
                AlertState.last_sent_at == row.last_sent_at).values(
                    severity=alert["severity"], pc_name=alert["pc_name"], message=alert["message"],
                    last_sent_at=now)))
        db.commit()
        return res.rowcount == 1

    @staticmethod
    def _release(db: Session, event: str, alert: dict, row: AlertState | None) -> None:
        """The send failed: undo the claim so the next pass tries again."""
        if event == "alert.opened":
            db.execute(delete(AlertState).where(AlertState.alert_id == alert["id"]))
            db.commit()
            return
        assert row is not None
        if event == "alert.resolved":
            db.add(AlertState(alert_id=row.alert_id, severity=row.severity, pc_name=row.pc_name,
                              message=row.message, opened_at=row.opened_at, last_sent_at=row.last_sent_at))
        else:
            db.execute(update(AlertState).where(AlertState.alert_id == alert["id"]).values(
                severity=row.severity, pc_name=row.pc_name, message=row.message, last_sent_at=row.last_sent_at))
        db.commit()

    # -- the test message --------------------------------------------------------------------------------------------
    def send_test(self, db: Session) -> dict:
        config = load_config(db, self.data_key)
        if config is None:
            return {"ok": False, "status": None, "error": "no webhook is configured"}
        try:
            status = deliver(config, event_payload("test", None, utcnow()), self.transport)
            _remember(db, True, status, None, "test")
            return {"ok": True, "status": status, "error": None}
        except WebhookSendError as e:
            _remember(db, False, None, str(e), "test")
            return {"ok": False, "status": None, "error": str(e)}

    # -- the background thread --------------------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="alert-notifier", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.run_once()
            except Exception as e:                      # the worker must outlive any single failure
                log.warning("alert notifier pass failed (%s)", type(e).__name__)
