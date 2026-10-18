"""The page for one station: its status and program, each project's settings history against what it runs, 30 days of
activity, its own alerts and its recent events. Nothing of any other station appears."""
from datetime import timedelta

from fastapi.testclient import TestClient

from central.audit import record
from central.manage import create_admin
from central.models import (ConfigVersion, Pc, PcAppliedProject, PcAssignment, PcDailyStat, Project, utcnow)


def ago(**kw):
    return utcnow() - timedelta(**kw)


def detail(admin_client, pc_id):
    r = admin_client.get(f"/admin/fleet/{pc_id}")
    assert r.status_code == 200, r.text
    return r.json()


def publish(app, pc_id, version, hours, pid="proj-1", name="Bank files", extra_versions=()):
    """Project `pid` at `version` (published `hours` ago), earlier versions in `extra_versions` (version, hours ago)."""
    with app.state.session_factory() as db:
        db.add(Project(id=pid, name=name, config_json="{}", current_version=version))
        db.commit()
        db.add(PcAssignment(pc_id=pc_id, project_id=pid))
        for v, h in [(version, hours), *extra_versions]:
            db.add(ConfigVersion(kind="project", owner_id=pid, version=v, config_json="{}",
                                 created_by="root", created_at=ago(hours=h)))
        db.commit()


def applied(app, pc_id, version, pid="proj-1"):
    with app.state.session_factory() as db:
        db.add(PcAppliedProject(pc_id=pc_id, project_id=pid, version=version))
        pc = db.get(Pc, pc_id)
        pc.reported_at, pc.app_version = utcnow(), "1.0.0"
        db.commit()


def stats(app, pc_id, days_ago, documents, errors=0):
    with app.state.session_factory() as db:
        db.add(PcDailyStat(pc_id=pc_id, day=(utcnow() - timedelta(days=days_ago)).date().isoformat(),
                           documents=documents, pages=documents * 4, batches=1, errors=errors))
        db.commit()


# -- access ---------------------------------------------------------------------------------------------------
def test_an_unknown_station_is_a_404_and_the_page_needs_a_session(enrolled, admin_client, client):
    assert admin_client.get("/admin/fleet/does-not-exist").status_code == 404
    assert TestClient(client.app).get(f"/admin/fleet/{enrolled['pc_id']}").status_code == 401


def test_an_auditor_can_read_it(enrolled, app, client):
    create_admin(app.state.session_factory, "watcher", "another long passphrase", role="auditor")
    r = client.post("/admin/login", json={"username": "watcher", "password": "another long passphrase"})
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert client.get(f"/admin/fleet/{enrolled['pc_id']}").status_code == 200


# -- the station ------------------------------------------------------------------------------------------------
def test_the_basic_facts(enrolled, admin_client, app):
    applied(app, enrolled["pc_id"], 1)
    pc = detail(admin_client, enrolled["pc_id"])["pc"]
    assert pc["id"] == enrolled["pc_id"] and pc["display_name"] == "PC-1" and pc["status"] == "online"
    assert pc["app_version"] == "1.0.0" and pc["registered_at"] and pc["last_seen_at"] and pc["created_at"]


# -- settings: published history against what the station runs ---------------------------------------------------
def test_each_project_shows_what_is_assigned_what_runs_and_the_recent_versions(enrolled, admin_client, app):
    publish(app, enrolled["pc_id"], version=5, hours=3, extra_versions=[(4, 50), (3, 120)])
    applied(app, enrolled["pc_id"], 4)
    body = detail(admin_client, enrolled["pc_id"])
    (project,) = body["projects"]
    assert (project["name"], project["assigned_version"], project["applied_version"], project["state"]) == (
        "Bank files", 5, 4, "behind")
    assert project["published_at"] is not None
    assert [v["version"] for v in project["history"]] == [5, 4, 3]               # newest first
    assert project["history"][0]["published_by"] == "root"
    assert body["rollout"] == "behind"


def test_the_history_is_the_last_five_versions_only(enrolled, admin_client, app):
    publish(app, enrolled["pc_id"], version=9, hours=1, extra_versions=[(v, 100 - v) for v in range(1, 9)])
    applied(app, enrolled["pc_id"], 9)
    (project,) = detail(admin_client, enrolled["pc_id"])["projects"]
    assert [v["version"] for v in project["history"]] == [9, 8, 7, 6, 5]


def test_a_station_with_no_projects_says_so(enrolled, admin_client):
    body = detail(admin_client, enrolled["pc_id"])
    assert body["projects"] == [] and body["rollout"] == "none"


# -- activity -----------------------------------------------------------------------------------------------------------
def test_thirty_days_of_activity_with_quiet_days_as_zeros(enrolled, admin_client, app):
    stats(app, enrolled["pc_id"], 0, 12, errors=2)
    stats(app, enrolled["pc_id"], 3, 7)
    body = detail(admin_client, enrolled["pc_id"])
    assert len(body["days"]) == 30 and body["days"][-1]["documents"] == 12 and body["days"][-4]["documents"] == 7
    assert sum(d["documents"] for d in body["days"]) == 19
    assert body["totals"] == {"documents": 19, "pages": 76, "batches": 2, "errors": 2}
    assert body["today"] == {"documents": 12, "pages": 48, "batches": 1, "errors": 2}


def test_a_station_that_reported_nothing_today_has_no_today(enrolled, admin_client):
    assert detail(admin_client, enrolled["pc_id"])["today"] is None


# -- alerts ------------------------------------------------------------------------------------------------------------------
def test_only_this_stations_alerts_appear(enrolled, admin_client, app):
    other = admin_client.post("/admin/pcs", json={"display_name": "Other"}).json()
    TestClient(app).post("/pcs/register", json={"code": other["enrollment_code"]})
    with app.state.session_factory() as db:
        db.get(Pc, enrolled["pc_id"]).last_seen_at = ago(hours=30)
        db.get(Pc, other["pc_id"]).last_seen_at = ago(hours=30)
        db.commit()
    mine = detail(admin_client, enrolled["pc_id"])["alerts"]
    assert [(a["kind"], a["pc_id"], a["severity"]) for a in mine] == [("offline", enrolled["pc_id"], "critical")]


def test_a_healthy_station_has_none(enrolled, admin_client):
    assert detail(admin_client, enrolled["pc_id"])["alerts"] == []


# -- events -----------------------------------------------------------------------------------------------------------------
def test_recent_events_cover_its_own_and_those_about_it_but_not_other_stations_or_poll_noise(enrolled, admin_client, app):
    other = admin_client.post("/admin/pcs", json={"display_name": "Other"}).json()
    with app.state.session_factory() as db:
        for _ in range(30):
            record(db, f"pc:{enrolled['pc_id']}", "config_pull", {"projects": {}})          # the poll noise
        record(db, f"pc:{other['pc_id']}", "pc_registered", {})                             # someone else's
        db.commit()
    admin_client.delete(f"/admin/pcs/{enrolled['pc_id']}/key")                              # an admin acting on it
    actions = [e["action"] for e in detail(admin_client, enrolled["pc_id"])["events"]]
    assert "pc_registered" in actions and "pc_key_revoked" in actions and "pc_created" in actions
    assert "config_pull" not in actions
    events = detail(admin_client, enrolled["pc_id"])["events"]
    assert all(other["pc_id"] not in str(e) for e in events)


def test_events_are_newest_first_and_capped(enrolled, admin_client, app):
    with app.state.session_factory() as db:
        for i in range(40):
            record(db, "admin:root", "pc_renamed", {"pc_id": enrolled["pc_id"], "n": i})
        db.commit()
    events = detail(admin_client, enrolled["pc_id"])["events"]
    assert len(events) == 25 and events[0]["detail"]["n"] == 39
