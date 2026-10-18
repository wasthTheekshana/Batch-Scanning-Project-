"""Fleet monitoring: what a PC reports with its heartbeat (app version, the project versions it has
applied, daily counts) and what the admin sees of it (status, rollout, activity). Counts only: no
document content ever reaches the server."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from central.manage import create_admin
from central.models import Pc, PcAppliedProject, PcAssignment, PcDailyStat, Project, utcnow


def today() -> str:
    return utcnow().date().isoformat()


def day(offset: int) -> str:
    return (utcnow().date() + timedelta(days=offset)).isoformat()


def beat(enrolled, **body):
    headers = {"Authorization": f"Bearer {enrolled['api_key']}"}
    return enrolled["client"].post(f"/pcs/{enrolled['pc_id']}/heartbeat", headers=headers,
                                   json=body or None)


def stats(documents=0, pages=0, batches=0, errors=0, on=None):
    return {"day": on or today(), "documents": documents, "pages": pages, "batches": batches,
            "errors": errors}


def project(app, pid="proj-1", name="Bank files", version=3, assign_to=None):
    with app.state.session_factory() as db:
        db.add(Project(id=pid, name=name, config_json="{}", current_version=version))
        db.commit()
        if assign_to:
            db.add(PcAssignment(pc_id=assign_to, project_id=pid))
            db.commit()


def fleet(admin_client):
    r = admin_client.get("/admin/fleet")
    assert r.status_code == 200, r.text
    return r.json()


def one(admin_client):
    return fleet(admin_client)["pcs"][0]


# -- what a PC may report ---------------------------------------------------------------------------
def test_a_heartbeat_without_a_body_still_works_as_before(enrolled):
    assert beat(enrolled).status_code == 204


def test_a_full_report_is_stored_and_shown(enrolled, admin_client, app):
    project(app, assign_to=enrolled["pc_id"], version=3)
    r = beat(enrolled, app_version="1.0.0", applied={"proj-1": 3},
             stats=stats(documents=12, pages=80, batches=3, errors=1))
    assert r.status_code == 204
    pc = one(admin_client)
    assert pc["app_version"] == "1.0.0" and pc["status"] == "online"
    assert pc["today"] == {"documents": 12, "pages": 80, "batches": 3, "errors": 1}
    assert pc["projects"] == [{"project_id": "proj-1", "name": "Bank files", "assigned_version": 3,
                               "applied_version": 3, "state": "current"}]


def test_a_second_report_replaces_the_first_instead_of_adding_to_it(enrolled, admin_client):
    beat(enrolled, stats=stats(documents=5, pages=10))
    beat(enrolled, stats=stats(documents=9, pages=22))                # the PC's running total for the day
    assert one(admin_client)["today"]["documents"] == 9


def test_projects_the_pc_no_longer_reports_are_forgotten(enrolled, app):
    beat(enrolled, applied={"a": 1, "b": 2})
    beat(enrolled, applied={"a": 1})
    with app.state.session_factory() as db:
        rows = {r.project_id for r in db.query(PcAppliedProject).filter_by(pc_id=enrolled["pc_id"])}
    assert rows == {"a"}


@pytest.mark.parametrize("body", [
    {"app_version": "1.0.0; DROP TABLE"},                      # not a version
    {"app_version": "x" * 40},
    {"applied": {"bad key!": 1}},
    {"applied": {"a": -1}},
    {"applied": {f"p{i}": 1 for i in range(201)}},             # more projects than anyone has
    {"stats": {"day": "yesterday", "documents": 1, "pages": 1, "batches": 1, "errors": 0}},
    {"stats": stats(documents=-5)},
    {"stats": stats(documents=10 ** 9)},
    {"stats": stats(on=day(-30))},                             # a clock that far off is junk
    {"stats": stats(on=day(10))},
])
def test_a_bad_report_is_refused_and_changes_nothing(enrolled, admin_client, body):
    beat(enrolled, app_version="1.0.0")
    assert beat(enrolled, **body).status_code == 422
    assert one(admin_client)["app_version"] == "1.0.0"


def test_a_report_needs_the_pcs_own_key(enrolled, app):
    other = TestClient(app)
    r = other.post(f"/pcs/{enrolled['pc_id']}/heartbeat", json={"app_version": "9.9.9"})
    assert r.status_code == 401


# -- status ---------------------------------------------------------------------------------------------
def test_status_online_offline_not_registered_and_key_revoked(enrolled, admin_client, app):
    admin_client.post("/admin/pcs", json={"display_name": "Never-enrolled"})
    by_name = {p["display_name"]: p for p in fleet(admin_client)["pcs"]}
    assert by_name["PC-1"]["status"] == "online" and by_name["Never-enrolled"]["status"] == "not_registered"
    with app.state.session_factory() as db:
        db.get(Pc, enrolled["pc_id"]).last_seen_at = utcnow() - timedelta(days=2)
        db.commit()
    assert {p["display_name"]: p["status"] for p in fleet(admin_client)["pcs"]}["PC-1"] == "offline"
    admin_client.delete(f"/admin/pcs/{enrolled['pc_id']}/key")
    assert {p["display_name"]: p["status"] for p in fleet(admin_client)["pcs"]}["PC-1"] == "key_revoked"


def test_the_summary_counts_pcs_by_status_and_app_version(enrolled, admin_client):
    beat(enrolled, app_version="1.0.0", stats=stats(documents=4, pages=9, batches=1, errors=2))
    admin_client.post("/admin/pcs", json={"display_name": "Second"})
    s = fleet(admin_client)["summary"]
    assert (s["total"], s["online"], s["not_registered"]) == (2, 1, 1)
    assert s["versions"] == {"1.0.0": 1, "unknown": 1}
    assert s["today"] == {"documents": 4, "pages": 9, "batches": 1, "errors": 2}


# -- rollout: has each PC applied the current settings? ---------------------------------------------------
def test_rollout_states(enrolled, admin_client, app):
    pc_id = enrolled["pc_id"]
    project(app, "p-current", "Current", 2, pc_id)
    project(app, "p-behind", "Behind", 5, pc_id)
    project(app, "p-missing", "Missing", 1, pc_id)
    beat(enrolled, applied={"p-current": 2, "p-behind": 4})
    pc = one(admin_client)
    states = {p["name"]: (p["state"], p["applied_version"]) for p in pc["projects"]}
    assert states == {"Current": ("current", 2), "Behind": ("behind", 4), "Missing": ("not_applied", None)}
    assert pc["rollout"] == "behind"
    assert fleet(admin_client)["summary"]["behind"] == 1


def test_a_pc_that_never_reported_has_an_unknown_rollout_not_a_false_alarm(enrolled, admin_client, app):
    project(app, assign_to=enrolled["pc_id"])
    pc = one(admin_client)                      # an older PC program: it never sends versions
    assert pc["rollout"] == "unknown" and pc["projects"][0]["state"] == "unknown"
    assert fleet(admin_client)["summary"]["behind"] == 0


def test_a_pc_with_nothing_assigned_has_no_rollout(enrolled, admin_client):
    beat(enrolled, applied={})
    assert one(admin_client)["rollout"] == "none"


def test_a_new_version_pushed_makes_the_pc_behind_until_it_reports_again(enrolled, admin_client, app):
    project(app, assign_to=enrolled["pc_id"], version=3)
    beat(enrolled, applied={"proj-1": 3})
    assert one(admin_client)["rollout"] == "current"
    with app.state.session_factory() as db:
        db.get(Project, "proj-1").current_version = 4
        db.commit()
    assert one(admin_client)["rollout"] == "behind"
    beat(enrolled, applied={"proj-1": 4})
    assert one(admin_client)["rollout"] == "current"


# -- activity -----------------------------------------------------------------------------------------------
def test_stats_cover_every_day_of_the_window_with_zeros_for_quiet_days(enrolled, admin_client, app):
    with app.state.session_factory() as db:
        db.add(PcDailyStat(pc_id=enrolled["pc_id"], day=day(-2), documents=7, pages=30, batches=2, errors=0))
        db.commit()
    beat(enrolled, stats=stats(documents=3, pages=11, batches=1, errors=1))
    r = admin_client.get("/admin/stats", params={"days": 5})
    assert r.status_code == 200
    body = r.json()
    assert [d["day"] for d in body["days"]] == [day(i) for i in range(-4, 1)]
    assert [d["documents"] for d in body["days"]] == [0, 0, 7, 0, 3]
    assert body["totals"] == {"documents": 10, "pages": 41, "batches": 3, "errors": 1}
    assert body["per_pc"][0]["display_name"] == "PC-1" and body["per_pc"][0]["documents"] == 10


def test_stats_add_up_across_pcs_and_rank_the_busiest_first(enrolled, admin_client, app):
    beat(enrolled, stats=stats(documents=2))
    other = admin_client.post("/admin/pcs", json={"display_name": "Busy"}).json()
    reg = TestClient(app).post("/pcs/register", json={"code": other["enrollment_code"]}).json()
    busy = {"pc_id": other["pc_id"], "api_key": reg["api_key"], "client": TestClient(app)}
    beat(busy, stats=stats(documents=20))
    body = admin_client.get("/admin/stats").json()
    assert body["totals"]["documents"] == 22
    assert [p["display_name"] for p in body["per_pc"]] == ["Busy", "PC-1"]


@pytest.mark.parametrize("days", [0, 91, -1])
def test_the_stats_window_is_bounded(admin_client, days):
    assert admin_client.get("/admin/stats", params={"days": days}).status_code == 422


# -- who may look ------------------------------------------------------------------------------------------------
def test_the_fleet_views_need_an_admin_session(client):
    assert client.get("/admin/fleet").status_code == 401
    assert client.get("/admin/stats").status_code == 401


def test_an_auditor_can_read_the_fleet_and_the_stats(app, client):
    create_admin(app.state.session_factory, "watcher", "another long passphrase", role="auditor")
    r = client.post("/admin/login", json={"username": "watcher", "password": "another long passphrase"})
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert client.get("/admin/fleet").status_code == 200
    assert client.get("/admin/stats").status_code == 200


# -- housekeeping --------------------------------------------------------------------------------------------------
def test_deleting_a_pc_removes_what_it_reported(enrolled, admin_client, app):
    beat(enrolled, applied={"a": 1}, stats=stats(documents=1))
    assert admin_client.delete(f"/admin/pcs/{enrolled['pc_id']}").status_code == 204
    with app.state.session_factory() as db:
        assert db.query(PcAppliedProject).count() == 0 and db.query(PcDailyStat).count() == 0


def test_old_daily_rows_are_pruned_as_new_ones_arrive(enrolled, app):
    with app.state.session_factory() as db:
        db.add(PcDailyStat(pc_id=enrolled["pc_id"], day=day(-500), documents=1, pages=1, batches=1, errors=0))
        db.commit()
    beat(enrolled, stats=stats(documents=1))
    with app.state.session_factory() as db:
        days = {r.day for r in db.query(PcDailyStat)}
    assert day(-500) not in days and today() in days
