"""PC rename/delete, audit-log reading and the dashboard overview."""
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from central.audit import record
from central.models import AuditEvent, Pc, utcnow


def new_pc(admin_client, name="PC-A"):
    r = admin_client.post("/admin/pcs", json={"display_name": name})
    assert r.status_code == 201
    return r.json()


def seen(app, pc_id, minutes_ago):
    with app.state.session_factory() as db:
        db.get(Pc, pc_id).last_seen_at = utcnow() - timedelta(minutes=minutes_ago)
        db.commit()


def audit(admin_client, **params):
    r = admin_client.get("/admin/audit", params=params)
    assert r.status_code == 200, r.text
    return r.json()


# -- rename ------------------------------------------------------------------------
def test_rename_a_pc(admin_client):
    pc = new_pc(admin_client)
    r = admin_client.patch(f"/admin/pcs/{pc['pc_id']}", json={"display_name": "Scan-Desk-1"})
    assert r.status_code == 200 and r.json()["display_name"] == "Scan-Desk-1"
    names = [p["display_name"] for p in admin_client.get("/admin/pcs").json()]
    assert names == ["Scan-Desk-1"]


def test_rename_to_an_existing_name_conflicts_and_unknown_pc_is_404(admin_client):
    new_pc(admin_client, "PC-A")
    other = new_pc(admin_client, "PC-B")
    assert admin_client.patch(f"/admin/pcs/{other['pc_id']}",
                              json={"display_name": "PC-A"}).status_code == 409
    assert admin_client.patch("/admin/pcs/nope",
                              json={"display_name": "X"}).status_code == 404
    assert admin_client.patch(f"/admin/pcs/{other['pc_id']}",
                              json={"display_name": ""}).status_code == 422


# -- delete ------------------------------------------------------------------------
def test_deleting_a_pc_removes_it_and_its_access(enrolled, admin_client):
    pc_id = enrolled["pc_id"]
    headers = {"Authorization": f"Bearer {enrolled['api_key']}"}
    assert enrolled["client"].post(f"/pcs/{pc_id}/heartbeat",
                                   headers=headers).status_code == 204
    assert admin_client.delete(f"/admin/pcs/{pc_id}").status_code == 204
    assert admin_client.get("/admin/pcs").json() == []
    assert enrolled["client"].post(f"/pcs/{pc_id}/heartbeat",
                                   headers=headers).status_code == 401
    assert admin_client.delete(f"/admin/pcs/{pc_id}").status_code == 404


def test_deleting_clears_pending_codes_and_assignments(app, admin_client):
    from central.models import EnrollmentCode, PcAssignment, Project
    pc = new_pc(admin_client)
    with app.state.session_factory() as db:
        db.add(Project(id="proj-1", name="P", config_json="{}"))
        db.commit()
        db.add(PcAssignment(pc_id=pc["pc_id"], project_id="proj-1"))
        db.commit()
    assert admin_client.delete(f"/admin/pcs/{pc['pc_id']}").status_code == 204
    with app.state.session_factory() as db:
        assert db.scalars(select(EnrollmentCode)).first() is None
        assert db.scalars(select(PcAssignment)).first() is None
        assert db.get(Project, "proj-1") is not None        # the project stays


def test_rename_and_delete_are_audited(admin_client):
    pc = new_pc(admin_client)
    admin_client.patch(f"/admin/pcs/{pc['pc_id']}", json={"display_name": "Renamed"})
    admin_client.delete(f"/admin/pcs/{pc['pc_id']}")
    actions = [e["action"] for e in audit(admin_client)["events"]]
    assert "pc_renamed" in actions and "pc_deleted" in actions


# -- audit log ----------------------------------------------------------------------
def test_audit_is_newest_first_with_parsed_detail(admin_client):
    new_pc(admin_client, "PC-A")
    events = audit(admin_client)["events"]
    assert events[0]["action"] == "pc_created"
    assert isinstance(events[0]["detail"], dict) and "pc_id" in events[0]["detail"]
    assert set(events[0]) == {"id", "at", "actor", "action", "detail"}
    assert events[0]["at"].endswith("Z")
    ids = [e["id"] for e in events]
    assert ids == sorted(ids, reverse=True)


def test_audit_pages_with_a_cursor(app, admin_client):
    with app.state.session_factory() as db:
        for n in range(7):
            record(db, "test", "ping", {"n": n})
        db.commit()
    first = audit(admin_client, limit=3, action="ping")
    assert [e["detail"]["n"] for e in first["events"]] == [6, 5, 4]
    second = audit(admin_client, limit=3, action="ping",
                   before_id=first["next_before_id"])
    assert [e["detail"]["n"] for e in second["events"]] == [3, 2, 1]
    last = audit(admin_client, limit=3, action="ping",
                 before_id=second["next_before_id"])
    assert [e["detail"]["n"] for e in last["events"]] == [0]
    assert last["next_before_id"] is None


def test_audit_filters_by_action_and_actor(app, admin_client):
    with app.state.session_factory() as db:
        record(db, "alice", "thing_a", {})
        record(db, "bob", "thing_b", {})
        db.commit()
    assert [e["actor"] for e in audit(admin_client, action="thing_a")["events"]] == ["alice"]
    assert [e["action"] for e in audit(admin_client, actor="bob")["events"]] == ["thing_b"]


def test_audit_limit_is_bounded_and_filters_are_validated(admin_client):
    assert admin_client.get("/admin/audit", params={"limit": 0}).status_code == 422
    assert admin_client.get("/admin/audit", params={"limit": 201}).status_code == 422
    assert admin_client.get("/admin/audit", params={"action": "a b;"}).status_code == 422
    assert admin_client.get("/admin/audit", params={"before_id": 0}).status_code == 422


def test_a_corrupt_detail_value_never_breaks_the_list(app, admin_client):
    with app.state.session_factory() as db:
        db.add(AuditEvent(actor="x", action="broken", detail="{not json"))
        db.commit()
    event = audit(admin_client, action="broken")["events"][0]
    assert event["detail"] == {}


def test_audit_actions_lists_each_action_once(app, admin_client):
    new_pc(admin_client, "PC-A")
    new_pc(admin_client, "PC-B")
    actions = admin_client.get("/admin/audit/actions").json()
    assert actions == sorted(set(actions)) and actions.count("pc_created") == 1


def test_audit_requires_login(app):
    assert TestClient(app).get("/admin/audit").status_code == 401
    assert TestClient(app).get("/admin/overview").status_code == 401


# -- overview --------------------------------------------------------------------------
def test_overview_counts_and_flags_pcs_that_stopped_checking_in(app, admin_client):
    fresh = new_pc(admin_client, "PC-fresh")
    stale = new_pc(admin_client, "PC-stale")
    never = new_pc(admin_client, "PC-never")
    seen(app, fresh["pc_id"], 1)
    seen(app, stale["pc_id"], 600)
    with app.state.session_factory() as db:
        for pc in db.scalars(select(Pc)):
            if pc.id != never["pc_id"]:
                pc.api_key_hash = "x" * 64          # registered
        db.commit()
    data = admin_client.get("/admin/overview").json()
    assert data["pcs"]["total"] == 3
    assert data["pcs"]["registered"] == 2
    assert data["pcs"]["online"] == 1
    assert data["pcs"]["offline"] == ["PC-stale"]       # registered, silent too long
    assert data["admins"] == {"total": 1, "active": 1}
    assert data["poll_interval_seconds"] > 0
    assert len(data["recent_audit"]) <= 10 and data["recent_audit"][0]["action"]


def test_pc_list_says_who_is_online(app, admin_client):
    pc = new_pc(admin_client, "PC-A")
    seen(app, pc["pc_id"], 1)
    row = admin_client.get("/admin/pcs").json()[0]
    assert row["online"] is True
    seen(app, pc["pc_id"], 600)
    assert admin_client.get("/admin/pcs").json()[0]["online"] is False
