"""What running it for real needs: a stuck queue is an alert, a station shows what it has sent, the Overview is not
buried in routine polls, results can be exported safely, and the server says when it cannot take documents."""
import csv
import io
import random
from datetime import timedelta

from central.alerts import BACKLOG_AFTER
from central.models import AuditEvent, utcnow
from tests.central.test_documents import FIELDS, SHA, keyed_app, login, world  # noqa: F401  (fixtures)


def beat(world, **docs):
    r = world.pc_client.post(f"/pcs/{world.pc_id}/heartbeat", json={"documents": docs})
    assert r.status_code == 204, r.text


def kinds(client):
    """The queue-related alerts only (the test station also has settings it has not applied)."""
    return {(a["kind"], a["severity"]) for a in client.get("/admin/alerts").json()["alerts"]
            if a["kind"] in ("backlog", "refused")}


def stamp(delta):
    return (utcnow() - delta).isoformat() + "Z"


# -- the station's queue ------------------------------------------------------------------------------------------
def test_a_queue_that_has_waited_too_long_is_an_alert_and_a_short_one_is_not(world):
    beat(world, waiting=3, refused=0, oldest_at=stamp(timedelta(minutes=20)))
    assert kinds(world.admin_client) == set()
    beat(world, waiting=3, refused=0, oldest_at=stamp(BACKLOG_AFTER + timedelta(minutes=1)))
    assert kinds(world.admin_client) == {("backlog", "warn")}
    beat(world, waiting=3, refused=0, oldest_at=stamp(timedelta(hours=30)))
    assert kinds(world.admin_client) == {("backlog", "critical")}
    beat(world, waiting=0, refused=0, oldest_at=None)
    assert kinds(world.admin_client) == set()


def test_refused_documents_are_an_alert_that_says_what_to_check(world):
    beat(world, waiting=0, refused=2, oldest_at=None)
    (alert,) = [a for a in world.admin_client.get("/admin/alerts").json()["alerts"] if a["kind"] == "refused"]
    assert alert["kind"] == "refused" and "project is assigned" in alert["message"]


def test_the_fleet_and_the_station_page_show_the_queue(world):
    beat(world, waiting=4, refused=1, oldest_at="2026-10-18T08:00:00Z")
    row = world.admin_client.get("/admin/fleet").json()["pcs"][0]["documents"]
    assert row == {"waiting": 4, "refused": 1, "oldest_at": "2026-10-18T08:00:00Z"}
    assert world.admin_client.get(f"/admin/fleet/{world.pc_id}").json()["queue"]["waiting"] == 4


def test_a_station_that_never_reports_a_queue_has_none_and_no_alert(world):
    assert world.admin_client.get("/admin/fleet").json()["pcs"][0]["documents"] is None
    assert kinds(world.admin_client) == set()


def test_a_bad_queue_report_is_refused(world):
    r = world.pc_client.post(f"/pcs/{world.pc_id}/heartbeat", json={"documents": {"waiting": -1, "refused": 0}})
    assert r.status_code == 422


def test_the_station_page_lists_what_it_has_sent(world):
    for i in range(10):
        world.meta(doc=f"d{i}", sha=f"{i:064x}")
        world.index(doc=f"d{i}", fields={"nic": f"55321678{i}V"})
    page = world.admin_client.get(f"/admin/fleet/{world.pc_id}").json()["documents"]
    assert page["held"] == 10 and len(page["recent"]) == 8 and page["recent"][0]["id"]


# -- routine polls do not bury real events ---------------------------------------------------------------------------
def test_the_overview_leaves_out_routine_polls(world):
    with world.app.state.session_factory() as db:
        db.add_all(AuditEvent(actor=f"pc:{world.pc_id}", action="config_pull", detail="{}") for _ in range(20))
        db.commit()
    actions = [e["action"] for e in world.admin_client.get("/admin/overview").json()["recent_audit"]]
    assert actions and "config_pull" not in actions


def test_old_poll_records_are_dropped_and_other_records_kept(world, monkeypatch):
    monkeypatch.setattr(random, "random", lambda: 0.0)                 # make the occasional clean-up happen now
    with world.app.state.session_factory() as db:
        db.add(AuditEvent(actor="pc:x", action="config_pull", detail="{}", at=utcnow() - timedelta(days=30)))
        db.add(AuditEvent(actor="admin:root", action="project_saved", detail="{}", at=utcnow() - timedelta(days=30)))
        db.commit()
    world.pc_client.get(f"/pcs/{world.pc_id}/config")
    with world.app.state.session_factory() as db:
        old = [e.action for e in db.query(AuditEvent).filter(AuditEvent.at < utcnow() - timedelta(days=20))]
    assert "config_pull" not in old and "project_saved" in old


# -- the server's own state ---------------------------------------------------------------------------------------------
def test_the_overview_says_whether_the_server_can_take_documents(world):
    world.meta()
    assert world.admin_client.get("/admin/overview").json()["server"] == {"data_key": True, "documents": 1}


# -- export ------------------------------------------------------------------------------------------------------------------
def rows_of(response):
    return list(csv.DictReader(io.StringIO(response.text)))


def test_the_export_has_what_the_search_shows_and_is_logged(world):
    world.meta()
    world.index()
    r = world.admin_client.get("/admin/documents/export.csv", params={"q": FIELDS["nic"]})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    (row,) = rows_of(r)
    assert row["nic"] == FIELDS["nic"] and row["full_name"] == "Kamal Perera" and row["station"] == "Galle desk"
    entry = world.admin_client.get("/admin/documents/access-log").json()["items"][0]
    assert entry["action"] == "export" and entry["detail"] == {"filters": ["any_field"], "rows": 1, "matched": 1}
    assert FIELDS["nic"] not in str(entry)


def test_a_value_that_would_run_as_a_spreadsheet_formula_is_neutralised(world):
    world.meta()
    world.index(fields={"nic": "=HYPERLINK(1)", "note": "+cmd", "full_name": "@x"})
    (row,) = rows_of(world.admin_client.get("/admin/documents/export.csv"))
    assert row["nic"].startswith("'=") and row["note"].startswith("'+") and row["full_name"].startswith("'@")


def test_a_searcher_exports_only_their_projects_and_a_bad_filter_is_refused(world):
    world.meta(doc="a", project="bank")
    world.index(doc="a", fields={"nic": "111111111V"})
    world.meta(doc="b", project="loans", sha=SHA[::-1])
    world.index(doc="b", fields={"nic": "222222222V"})
    made = world.admin_client.post("/admin/users", json={"username": "finder", "password": "a long passphrase here",
                                                        "role": "searcher"}).json()
    world.admin_client.put(f"/admin/users/{made['id']}/projects", json={"project_ids": ["bank"]})
    searcher = login(world.app, "finder", "a long passphrase here")
    assert [r["nic"] for r in rows_of(searcher.get("/admin/documents/export.csv"))] == ["111111111V"]
    assert searcher.get("/admin/documents/export.csv", params={"q": "ab"}).status_code == 422


def test_a_field_name_cannot_start_like_a_formula(world):
    world.meta()
    assert world.index(fields={"-2+3": "x"}).status_code == 422
    assert world.index(fields={".hidden": "x"}).status_code == 422
    assert world.index(fields={"nic-2": "x1234"}).status_code == 200


# -- deleting a document ---------------------------------------------------------------------------------------------
def delete(client, doc_id, reason="entered by mistake"):
    return client.post(f"/admin/documents/{doc_id}/delete", json={"reason": reason})


def first(world):
    return world.admin_client.get("/admin/documents").json()["items"][0]["id"]


def test_an_admin_deletes_a_document_and_everything_about_it_goes(world):
    from sqlalchemy import text
    world.meta()
    world.index()
    doc_id = first(world)
    assert delete(world.admin_client, doc_id).json() == {"deleted": True, "document": doc_id}
    assert world.admin_client.get("/admin/documents").json()["total"] == 0
    assert world.admin_client.get("/admin/documents", params={"q": FIELDS["nic"]}).json()["total"] == 0
    assert world.admin_client.get(f"/admin/documents/{doc_id}").status_code == 404
    assert world.admin_client.get("/admin/overview").json()["server"]["documents"] == 0
    assert world.admin_client.get(f"/admin/fleet/{world.pc_id}").json()["documents"]["held"] == 0
    with world.app.state.engine.connect() as c:
        for table in ("index_revisions", "index_lookup", "document_files"):
            assert c.execute(text(f"select count(*) from {table}")).scalar() == 0, table


def test_the_deletion_is_audited_with_its_reason_and_the_access_log(world):
    world.meta()
    world.index()
    doc_id = first(world)
    delete(world.admin_client, doc_id, "customer withdrew the application")
    events = world.admin_client.get("/admin/audit", params={"action": "document_deleted"}).json()["events"]
    assert events[0]["actor"] == "admin:root" and events[0]["detail"]["reason"] == "customer withdrew the application"
    assert FIELDS["nic"] not in str(events)
    assert world.admin_client.get("/admin/documents/access-log").json()["items"][0]["action"] == "delete"


def test_only_an_admin_can_delete_and_a_reason_is_required(world):
    world.meta()
    world.index()
    doc_id = first(world)
    assert delete(world.admin_client, doc_id, "no").status_code == 422
    for role in ("auditor", "searcher"):
        world.admin_client.post("/admin/users", json={"username": role, "password": "a long passphrase here", "role": role})
        who = login(world.app, role, "a long passphrase here")
        assert delete(who, doc_id).status_code == 403, role
        assert delete(who, "no-such-id").status_code == 403, role        # the same answer: no hint whether it exists
    assert world.admin_client.get(f"/admin/documents/{doc_id}").status_code == 200
    assert delete(world.admin_client, "no-such-id").status_code == 404


def test_a_deleted_document_is_not_accepted_again_and_the_station_drops_it(world, tmp_path):
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from core.central_client import CentralClient
    from core.central_documents import DocumentPusher
    from data.db import Database
    world.meta()
    world.index()
    delete(world.admin_client, first(world))
    assert world.meta().status_code == 410 and world.index().status_code == 410
    assert world.admin_client.get("/admin/documents").json()["total"] == 0

    http = world.pc_client
    transport = lambda method, url, headers, body, timeout: (lambda r: (r.status_code, r.content))(          # noqa: E731
        http.request(method, url.removeprefix("https://central.test"), headers=headers, content=body))
    db = Database(tmp_path / "pc.db")
    db.insert_project("bank", "Bank", {})
    db.insert_batch("b-1", "bank", "kamal")
    state = SimpleNamespace(settings=SimpleNamespace(server_url="https://central.test", pc_id=world.pc_id, auth_failed=False),
                            is_registered=lambda: True, api_key=lambda: world.pc_client.headers["Authorization"][7:])
    db.outbox_gate = state.is_registered
    db.insert_document("doc-1", "b-1", "bank", extracted_fields={"nic": "1"}, created_at=None) if False else \
        db.insert_document("doc-1", "b-1", "bank", extracted_fields={"nic": "1"})
    pusher = DocumentPusher(db, state, client_factory=lambda url, t: CentralClient(url, transport=transport),
                            now=lambda: datetime.now(timezone.utc))
    result = pusher.push_once()
    assert (result.deleted, result.sent, result.refused) == (1, 0, 0)
    assert db.outbox_stats() == {"waiting": 0, "failed": 0, "oldest": None, "last_sent_at": None, "last_error": None}
    db.close()
