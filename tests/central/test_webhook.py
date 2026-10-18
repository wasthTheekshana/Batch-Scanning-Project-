"""Alerts by webhook: the address is checked and stored encrypted, events are sent once, a failure is retried, and
the address and secret never come back out of the API or into the audit log."""
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from central import notify
from central.app import create_app
from central.crypto import DataKey, generate_key
from central.manage import create_admin
from central.models import AlertState, AuditEvent, Pc, Setting, utcnow
from central.notify import Notifier, WebhookConfig, WebhookConfigError, WebhookSendError
from central.settings import Settings

URL = "https://hooks.example.com/services/T000/B000/SECRETTOKEN123"
SECRET = "shared-signing-secret"


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """The webhook host resolves to a public address (a documentation range), no real DNS needed."""
    monkeypatch.setattr(notify, "_resolve", lambda host, port: ["203.0.113.10"])


class Receiver:
    """Stands in for the network: records every message and can be told to fail."""

    def __init__(self):
        self.calls, self.status, self.error = [], 200, None

    def __call__(self, url, body, headers, timeout=10):
        if self.error:
            raise self.error
        self.calls.append({"url": url, "body": json.loads(body), "raw": body, "headers": headers})
        return self.status

    @property
    def events(self):
        return [c["body"]["event"] for c in self.calls]


@pytest.fixture()
def key():
    return DataKey(generate_key())


@pytest.fixture()
def keyed(tmp_path, key):
    """An app that has a data key, an admin session, and a receiver wired in as the transport."""
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'w.db'}", session_secret="x" * 40,
                              cookie_secure=False, data_key=key))
    create_admin(app.state.session_factory, "root", "correct horse battery")
    receiver = Receiver()
    app.state.notifier.transport = receiver
    with TestClient(app) as client:
        r = client.post("/admin/login", json={"username": "root", "password": "correct horse battery"})
        client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        yield type("K", (), dict(app=app, client=client, receiver=receiver, notifier=app.state.notifier, key=key))


def configure(k, **over):
    body = {"url": URL, "secret": SECRET, "enabled": True, "min_level": "warn", **over}
    r = k.client.put("/admin/webhook", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def quiet_station(k, name="Galle", hours=30):
    """A registered station silent for `hours`: one offline alert (warning, critical after a day)."""
    pc = k.client.post("/admin/pcs", json={"display_name": name}).json()
    TestClient(k.app).post("/pcs/register", json={"code": pc["enrollment_code"]})
    set_seen(k, pc["pc_id"], hours)
    return pc["pc_id"]


def set_seen(k, pc_id, hours):
    with k.app.state.session_factory() as db:
        db.get(Pc, pc_id).last_seen_at = utcnow() - timedelta(hours=hours)
        db.commit()


def state_rows(k):
    with k.app.state.session_factory() as db:
        return {r.alert_id: r.severity for r in db.query(AlertState)}


# ===================================== the address ====================================================================
def test_a_good_https_address_gives_its_host():
    assert notify.check_url(URL) == "hooks.example.com"


@pytest.mark.parametrize("url", ["http://hooks.example.com/x", "ftp://hooks.example.com/x", "hooks.example.com/x", "",
                                 "https://user:pw@hooks.example.com/x", "https:///nohost", "https://hooks.example.com/a b",
                                 "https://hooks.example.com:99999/x", "https://hooks.example.com/x\nHost: evil"])
def test_a_bad_address_is_refused_with_a_message_that_does_not_quote_it(url):
    with pytest.raises(WebhookConfigError) as caught:
        notify.check_url(url)
    assert "hooks.example.com" not in str(caught.value)


@pytest.mark.parametrize("ip", ["127.0.0.1", "::1", "169.254.169.254", "0.0.0.0", "224.0.0.1"])
def test_an_address_that_resolves_to_this_machine_or_cloud_metadata_is_refused(monkeypatch, ip):
    monkeypatch.setattr(notify, "_resolve", lambda host, port: [ip])
    with pytest.raises(WebhookConfigError, match="not allowed"):
        notify.check_url(URL)


def test_a_private_network_receiver_is_allowed_unless_the_bank_blocks_them(monkeypatch):
    monkeypatch.setattr(notify, "_resolve", lambda host, port: ["10.1.2.3"])
    assert notify.check_url(URL) == "hooks.example.com"
    with pytest.raises(WebhookConfigError):
        notify.check_url(URL, block_private=True)


def test_one_bad_answer_among_several_refuses_the_address(monkeypatch):
    monkeypatch.setattr(notify, "_resolve", lambda host, port: ["203.0.113.10", "127.0.0.1"])
    with pytest.raises(WebhookConfigError):
        notify.check_url(URL)


def test_http_and_local_receivers_are_a_development_switch(monkeypatch):
    monkeypatch.setattr(notify, "_resolve", lambda host, port: ["127.0.0.1"])
    assert notify.check_url("http://localhost:9000/hook", allow_http=True) == "localhost"


def test_an_address_that_does_not_resolve_is_refused(monkeypatch):
    def nothing(host, port):
        raise WebhookConfigError("the address does not resolve to a server")
    monkeypatch.setattr(notify, "_resolve", nothing)
    with pytest.raises(WebhookConfigError, match="resolve"):
        notify.check_url(URL)


# ===================================== the message ===================================================================
ALERT = {"id": "offline:pc-1", "kind": "offline", "severity": "critical", "pc_id": "pc-1", "pc_name": "Galle",
         "message": "Stopped checking in", "since": "2026-10-16T08:00:00Z"}


def test_messages_have_ready_to_read_text_and_the_alert_in_full():
    now = utcnow()
    opened = notify.event_payload("alert.opened", ALERT, now)
    assert opened["text"] == "[Critical] Galle: Stopped checking in" and opened["alert"] == ALERT
    assert notify.event_payload("alert.resolved", ALERT, now)["text"] == "Resolved: Galle: Stopped checking in"
    assert "reminder" in notify.event_payload("alert.reminder", ALERT, now)["text"]
    assert "alert" not in notify.event_payload("test", None, now) and "test message" in notify.event_payload("test", None, now)["text"]


def test_the_signature_covers_the_timestamp_and_the_body():
    body = b'{"event":"test"}'
    one = notify.sign("s3cret", "1760000000", body)
    assert one.startswith("sha256=") and len(one) == 7 + 64
    assert one != notify.sign("s3cret", "1760000001", body) != notify.sign("other", "1760000000", body)
    assert one != notify.sign("s3cret", "1760000000", body + b" ")


def test_the_headers_carry_the_event_and_a_signature_only_when_there_is_a_secret():
    rx = Receiver()
    notify.deliver(WebhookConfig(URL, None, True, "warn"), notify.event_payload("test", None, utcnow()), rx)
    assert "X-DOK-Signature" not in rx.calls[0]["headers"] and rx.calls[0]["headers"]["X-DOK-Event"] == "test"
    notify.deliver(WebhookConfig(URL, SECRET, True, "warn"), notify.event_payload("test", None, utcnow()), rx)
    sent = rx.calls[1]
    assert sent["headers"]["X-DOK-Signature"] == notify.sign(SECRET, sent["headers"]["X-DOK-Timestamp"], sent["raw"])


@pytest.mark.parametrize("status", [301, 400, 404, 500, 503])
def test_anything_but_a_2xx_answer_is_a_failure(status):
    rx = Receiver()
    rx.status = status
    with pytest.raises(WebhookSendError, match=str(status)):
        notify.deliver(WebhookConfig(URL, None, True, "warn"), notify.event_payload("test", None, utcnow()), rx)


# ===================================== which events =======================================================================
def row(severity="warn", ago=timedelta(0), name="Galle", message="Stopped checking in"):
    return AlertState(alert_id="offline:pc-1", severity=severity, pc_name=name, message=message,
                      opened_at=utcnow() - ago, last_sent_at=utcnow() - ago)


def alert(severity="warn", id="offline:pc-1"):
    return {**ALERT, "id": id, "severity": severity}


def names(result):
    return [e[0] for e in result[0]]


def test_a_new_alert_opens_and_an_unchanged_one_says_nothing():
    now = utcnow()
    assert names(notify.plan([alert()], {}, "warn", now)) == ["alert.opened"]
    assert names(notify.plan([alert()], {"offline:pc-1": row()}, "warn", now)) == []


def test_becoming_critical_escalates_once():
    plan = notify.plan([alert("critical")], {"offline:pc-1": row("warn")}, "warn", utcnow())
    assert names(plan) == ["alert.escalated"]


def test_a_critical_alert_reminds_every_twelve_hours_but_a_warning_never_does():
    now = utcnow()
    assert names(notify.plan([alert("critical")], {"offline:pc-1": row("critical", timedelta(hours=11))}, "warn", now)) == []
    assert names(notify.plan([alert("critical")], {"offline:pc-1": row("critical", timedelta(hours=13))}, "warn", now)) == ["alert.reminder"]
    assert names(notify.plan([alert("warn")], {"offline:pc-1": row("warn", timedelta(days=3))}, "warn", now)) == []


def test_an_alert_that_is_gone_is_resolved_with_the_station_name_it_was_announced_under():
    events, forget = notify.plan([], {"offline:pc-1": row(name="Galle")}, "warn", utcnow())
    assert [e[0] for e in events] == ["alert.resolved"] and forget == []
    assert events[0][1]["pc_name"] == "Galle" and events[0][1]["kind"] == "offline" and events[0][1]["pc_id"] == "pc-1"


def test_alerts_below_the_chosen_level_are_not_announced():
    assert names(notify.plan([alert("info", "errors:pc-1")], {}, "warn", utcnow())) == []
    assert names(notify.plan([alert("warn")], {}, "critical", utcnow())) == []
    assert names(notify.plan([alert("info", "errors:pc-1")], {}, "info", utcnow())) == ["alert.opened"]


def test_raising_the_level_forgets_a_still_present_alert_without_a_message():
    events, forget = notify.plan([alert("warn")], {"offline:pc-1": row("warn")}, "critical", utcnow())
    assert events == [] and forget == ["offline:pc-1"]


# ===================================== the worker, against the real alert rules ==============================================
def test_an_alert_is_announced_once_and_then_resolved_once(keyed):
    configure(keyed)
    pc = quiet_station(keyed)
    assert keyed.notifier.run_once() == {"sent": 1, "failed": 0, "skipped": None}
    assert keyed.receiver.events == ["alert.opened"]
    assert keyed.receiver.calls[0]["body"]["alert"]["pc_name"] == "Galle"
    keyed.notifier.run_once()
    assert keyed.receiver.events == ["alert.opened"]                       # nothing new: nothing sent
    set_seen(keyed, pc, 0)                                                 # the station checks in again
    keyed.notifier.run_once()
    assert keyed.receiver.events == ["alert.opened", "alert.resolved"] and state_rows(keyed) == {}
    assert keyed.receiver.calls[1]["body"]["text"] == "Resolved: Galle: Stopped checking in"


def test_a_warning_that_becomes_critical_is_announced_again_as_an_escalation(keyed):
    configure(keyed)
    pc = quiet_station(keyed, hours=2)
    keyed.notifier.run_once()
    set_seen(keyed, pc, 30)
    keyed.notifier.run_once()
    assert keyed.receiver.events == ["alert.opened", "alert.escalated"]
    assert state_rows(keyed) == {f"offline:{pc}": "critical"}


def test_a_failed_send_is_retried_on_the_next_pass_and_not_lost(keyed):
    configure(keyed)
    quiet_station(keyed)
    keyed.receiver.error = WebhookSendError("could not connect")
    assert keyed.notifier.run_once()["failed"] == 1 and state_rows(keyed) == {}
    keyed.receiver.error = None
    assert keyed.notifier.run_once()["sent"] == 1 and keyed.receiver.events == ["alert.opened"]


def test_a_receiver_that_answers_500_is_a_failure_with_a_plain_reason(keyed):
    configure(keyed)
    quiet_station(keyed)
    keyed.receiver.status = 500
    assert keyed.notifier.run_once()["failed"] == 1
    status = keyed.client.get("/admin/webhook").json()
    assert status["last_result"]["ok"] is False and "500" in status["last_result"]["error"]


def test_two_server_processes_cannot_announce_the_same_alert_twice(keyed):
    configure(keyed)
    quiet_station(keyed)
    other = Notifier(keyed.app.state.session_factory, keyed.key, transport=keyed.receiver)
    with keyed.app.state.session_factory() as db:
        first = notify.plan(notify.compute_alerts(db, timedelta(minutes=10)), {}, "warn", utcnow())[0][0]
        assert Notifier._claim(db, first[0], first[1], first[2], utcnow()) is True       # process A claims it
    with keyed.app.state.session_factory() as db:
        assert Notifier._claim(db, first[0], first[1], first[2], utcnow()) is False      # process B is too late
    assert other.run_once()["sent"] == 0 and keyed.receiver.calls == []                  # and neither sends again


def test_nothing_is_sent_while_it_is_off_not_set_up_or_the_server_has_no_key(keyed, tmp_path):
    quiet_station(keyed)
    assert keyed.notifier.run_once()["skipped"] == "not_configured"
    configure(keyed, enabled=False)
    assert keyed.notifier.run_once()["skipped"] == "disabled"
    configure(keyed)
    blind = Notifier(keyed.app.state.session_factory, None, transport=keyed.receiver)
    assert blind.run_once()["skipped"] == "not_configured" and keyed.receiver.calls == []


def test_switching_it_off_and_on_again_starts_clean(keyed):
    configure(keyed)
    pc = quiet_station(keyed)
    keyed.notifier.run_once()
    configure(keyed, enabled=False)
    assert state_rows(keyed) == {}
    set_seen(keyed, pc, 0)                                                 # resolved while it was off: no stale message
    configure(keyed)
    keyed.notifier.run_once()
    assert keyed.receiver.events == ["alert.opened"]


def test_only_alerts_at_or_above_the_chosen_level_are_announced(keyed):
    configure(keyed, min_level="critical")
    pc = quiet_station(keyed, hours=2)                                     # a warning
    keyed.notifier.run_once()
    assert keyed.receiver.calls == []
    set_seen(keyed, pc, 30)                                                # now critical
    keyed.notifier.run_once()
    assert keyed.receiver.events == ["alert.opened"]


def test_the_message_is_signed_with_the_stored_secret(keyed):
    configure(keyed)
    quiet_station(keyed)
    keyed.notifier.run_once()
    sent = keyed.receiver.calls[0]
    assert sent["headers"]["X-DOK-Signature"] == notify.sign(SECRET, sent["headers"]["X-DOK-Timestamp"], sent["raw"])
    assert sent["url"] == URL


# ===================================== the settings API =====================================================================
def test_nothing_is_set_up_at_first(keyed):
    status = keyed.client.get("/admin/webhook").json()
    assert status["configured"] is False and status["enabled"] is False and status["key_available"] is True
    assert status["host"] is None and status["last_result"] is None


def test_the_address_and_secret_never_come_back_out_of_the_api(keyed):
    saved = configure(keyed)
    assert saved["host"] == "hooks.example.com" and saved["has_secret"] is True and saved["enabled"] is True
    for text in (json.dumps(saved), keyed.client.get("/admin/webhook").text):
        assert "SECRETTOKEN123" not in text and SECRET not in text and "/services/" not in text


def test_the_address_and_secret_are_stored_encrypted(keyed):
    configure(keyed)
    with keyed.app.state.session_factory() as db:
        stored = " ".join(s.value for s in db.query(Setting) if s.key.startswith("webhook."))
    assert "SECRETTOKEN123" not in stored and SECRET not in stored and "hooks.example.com" not in stored


def test_the_audit_log_names_the_host_but_never_the_address_or_secret(keyed):
    configure(keyed)
    keyed.client.post("/admin/webhook/test")
    with keyed.app.state.session_factory() as db:
        texts = [f"{e.action} {e.detail}" for e in db.query(AuditEvent) if e.action.startswith("webhook")]
    assert any("webhook_configured" in t and "hooks.example.com" in t for t in texts)
    assert all("SECRETTOKEN123" not in t and SECRET not in t for t in texts)


@pytest.mark.parametrize("url", ["http://hooks.example.com/x", "not a url", "https://user:pw@hooks.example.com/x"])
def test_a_bad_address_is_refused_and_not_echoed(keyed, url):
    r = keyed.client.put("/admin/webhook", json={"url": url, "enabled": True})
    assert r.status_code == 422 and "hooks.example.com" not in r.text
    assert keyed.client.get("/admin/webhook").json()["configured"] is False


def test_it_cannot_be_switched_on_without_an_address(keyed):
    r = keyed.client.put("/admin/webhook", json={"enabled": True, "min_level": "warn"})
    assert r.status_code == 422 and "address" in r.json()["detail"]


def test_an_unknown_level_is_refused(keyed):
    assert keyed.client.put("/admin/webhook", json={"url": URL, "min_level": "loud"}).status_code == 422


def test_leaving_the_secret_out_keeps_it_and_an_empty_one_removes_it(keyed):
    configure(keyed)
    keep = keyed.client.put("/admin/webhook", json={"enabled": True, "min_level": "critical"}).json()
    assert keep["has_secret"] is True and keep["host"] == "hooks.example.com" and keep["min_level"] == "critical"
    gone = keyed.client.put("/admin/webhook", json={"secret": "", "enabled": True}).json()
    assert gone["has_secret"] is False and gone["configured"] is True


def test_removing_the_address_switches_it_off_and_forgets_everything(keyed):
    configure(keyed)
    quiet_station(keyed)
    keyed.notifier.run_once()
    cleared = keyed.client.put("/admin/webhook", json={"url": "", "enabled": True}).json()
    assert cleared["configured"] is False and cleared["enabled"] is False and cleared["has_secret"] is False
    assert state_rows(keyed) == {}


def test_without_a_data_key_the_server_refuses_to_store_the_address(admin_client):
    r = admin_client.put("/admin/webhook", json={"url": URL, "enabled": True})
    assert r.status_code == 409 and "CENTRAL_DATA_KEY" in r.json()["detail"]
    assert admin_client.get("/admin/webhook").json()["key_available"] is False
    assert admin_client.post("/admin/webhook/test").status_code == 409


def test_the_test_message_reports_success_and_failure(keyed):
    assert keyed.client.post("/admin/webhook/test").json() == {"ok": False, "status": None, "error": "no webhook is configured"}
    configure(keyed)
    ok = keyed.client.post("/admin/webhook/test").json()
    assert ok == {"ok": True, "status": 200, "error": None} and keyed.receiver.events == ["test"]
    keyed.receiver.error = WebhookSendError("timed out")
    bad = keyed.client.post("/admin/webhook/test").json()
    assert bad["ok"] is False and bad["error"] == "timed out"
    assert keyed.client.get("/admin/webhook").json()["last_result"]["error"] == "timed out"


def test_an_auditor_may_look_but_not_change_or_test(keyed):
    create_admin(keyed.app.state.session_factory, "watcher", "another long passphrase", role="auditor")
    other = TestClient(keyed.app)
    r = other.post("/admin/login", json={"username": "watcher", "password": "another long passphrase"})
    other.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert other.get("/admin/webhook").status_code == 200
    assert other.put("/admin/webhook", json={"url": URL}).status_code == 403
    assert other.post("/admin/webhook/test").status_code == 403


def test_the_webhook_pages_need_a_session(client):
    assert client.get("/admin/webhook").status_code == 401
    assert client.put("/admin/webhook", json={}).status_code in (401, 403)
    assert client.post("/admin/webhook/test").status_code in (401, 403)


# -- the address is checked again at send time, and the vetted address is the one connected to -------------------
def _listener():
    import http.server
    import threading
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append((self.headers.get("Host"), self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, seen


def test_a_name_that_was_re_pointed_after_saving_is_not_connected_to(monkeypatch):
    server, seen = _listener()
    try:
        # saved while public, but at send time the name points at this machine (DNS rebinding)
        monkeypatch.setattr(notify, "_resolve", lambda host, port: ["127.0.0.1"])
        with pytest.raises(WebhookSendError, match="no longer acceptable"):
            notify.http_post(f"https://hooks.example.com:{server.server_port}/x", b"{}", {})
        assert seen == []
    finally:
        server.shutdown()


def test_the_connection_goes_to_the_vetted_address_and_names_the_host(monkeypatch):
    server, seen = _listener()
    try:
        answers = iter([["127.0.0.1"], ["198.51.100.7"]])           # a second look would get another address
        monkeypatch.setattr(notify, "_resolve", lambda host, port: next(answers))
        status = notify.http_post(f"http://hooks.example.com:{server.server_port}/x", b'{"a":1}',
                                  {"Content-Type": "application/json"}, allow_http=True)
        assert status == 204 and seen == [(f"hooks.example.com:{server.server_port}", b'{"a":1}')]
    finally:
        server.shutdown()
