"""Alerts: what needs an administrator's attention, how serious it is, and for how long.
A station still inside the grace window raises nothing: one missed poll is not an incident."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from central.manage import create_admin
from central.models import (ConfigVersion, Pc, PcAppliedProject, PcAssignment, PcDailyStat,
                            Project, utcnow)


def ago(**kw):
    return utcnow() - timedelta(**kw)


def alerts(admin_client):
    r = admin_client.get("/admin/alerts")
    assert r.status_code == 200, r.text
    return r.json()


def kinds(admin_client):
    return sorted((a["kind"], a["severity"]) for a in alerts(admin_client)["alerts"])


def publish(app, pc_id, version=3, published_ago=timedelta(hours=1), pid="proj-1", name="Bank files"):
    with app.state.session_factory() as db:
        db.add(Project(id=pid, name=name, config_json="{}", current_version=version))
        db.commit()                                   # the project must exist before it is assigned
        db.add(PcAssignment(pc_id=pc_id, project_id=pid))
        db.add(ConfigVersion(kind="project", owner_id=pid, version=version, config_json="{}",
                             created_by="root", created_at=utcnow() - published_ago))
        db.commit()


def applied(app, pc_id, version, pid="proj-1"):
    with app.state.session_factory() as db:
        db.add(PcAppliedProject(pc_id=pc_id, project_id=pid, version=version))
        db.get(Pc, pc_id).reported_at = utcnow()
        db.commit()


def last_seen(app, pc_id, **kw):
    with app.state.session_factory() as db:
        db.get(Pc, pc_id).last_seen_at = ago(**kw)
        db.commit()


# -- nothing wrong ---------------------------------------------------------------------------------------
def test_a_healthy_fleet_has_no_alerts(enrolled, admin_client, app):
    publish(app, enrolled["pc_id"], version=3)
    applied(app, enrolled["pc_id"], 3)
    body = alerts(admin_client)
    assert body["alerts"] == [] and body["counts"] == {"critical": 0, "warn": 0, "info": 0}


# -- a station that stopped checking in ------------------------------------------------------------------------
def test_a_station_silent_for_a_while_is_a_warning_and_for_a_day_critical(enrolled, admin_client, app):
    last_seen(app, enrolled["pc_id"], hours=2)
    (alert,) = alerts(admin_client)["alerts"]
    assert (alert["kind"], alert["severity"], alert["pc_name"]) == ("offline", "warn", "PC-1")
    assert alert["since"] is not None and alert["id"] == f"offline:{enrolled['pc_id']}"
    last_seen(app, enrolled["pc_id"], hours=30)
    assert kinds(admin_client) == [("offline", "critical")]


def test_one_missed_poll_is_inside_the_grace_window_and_raises_nothing(enrolled, admin_client, app):
    last_seen(app, enrolled["pc_id"], minutes=4)
    assert alerts(admin_client)["alerts"] == []


# -- settings that were published but not applied ----------------------------------------------------------------
def test_settings_not_applied_long_after_publishing_is_an_alert(enrolled, admin_client, app):
    publish(app, enrolled["pc_id"], version=5, published_ago=timedelta(hours=3))
    applied(app, enrolled["pc_id"], 4)
    (alert,) = alerts(admin_client)["alerts"]
    assert (alert["kind"], alert["severity"]) == ("behind", "warn")
    assert "Bank files" in alert["message"] and "v5" in alert["message"] and "v4" in alert["message"]
    assert alert["id"] == f"behind:{enrolled['pc_id']}:proj-1"
    assert alert["since"] is not None                                   # when version 5 was published


def test_a_version_published_a_moment_ago_is_not_yet_an_alert(enrolled, admin_client, app):
    publish(app, enrolled["pc_id"], version=5, published_ago=timedelta(minutes=2))
    applied(app, enrolled["pc_id"], 4)
    assert alerts(admin_client)["alerts"] == []


def test_not_applied_for_a_day_is_critical(enrolled, admin_client, app):
    publish(app, enrolled["pc_id"], version=2, published_ago=timedelta(hours=30))
    applied(app, enrolled["pc_id"], 1)
    assert kinds(admin_client) == [("behind", "critical")]


def test_a_project_the_station_never_applied_says_so(enrolled, admin_client, app):
    publish(app, enrolled["pc_id"], version=2, published_ago=timedelta(hours=3))
    with app.state.session_factory() as db:
        db.get(Pc, enrolled["pc_id"]).reported_at = utcnow()          # it reports, but without this project
        db.commit()
    (alert,) = alerts(admin_client)["alerts"]
    assert alert["kind"] == "behind" and "not applied yet" in alert["message"]


def test_a_station_that_never_reports_is_not_called_behind(enrolled, admin_client, app):
    publish(app, enrolled["pc_id"], version=5, published_ago=timedelta(hours=40))
    assert alerts(admin_client)["alerts"] == []                        # an older program: unknown, not behind


def test_an_offline_station_gets_the_offline_alert_not_one_per_project(enrolled, admin_client, app):
    publish(app, enrolled["pc_id"], version=5, published_ago=timedelta(hours=40))
    applied(app, enrolled["pc_id"], 1)
    last_seen(app, enrolled["pc_id"], hours=3)
    assert kinds(admin_client) == [("offline", "warn")]


# -- errors, revoked access, never connected ----------------------------------------------------------------------
def error_day(app, pc_id, errors):
    with app.state.session_factory() as db:
        db.add(PcDailyStat(pc_id=pc_id, day=utcnow().date().isoformat(), documents=20, pages=60,
                           batches=3, errors=errors))
        db.commit()


@pytest.mark.parametrize("errors, severity", [(1, "info"), (4, "info"), (5, "warn"), (30, "warn")])
def test_errors_today_are_info_until_there_are_several(enrolled, admin_client, app, errors, severity):
    error_day(app, enrolled["pc_id"], errors)
    (alert,) = alerts(admin_client)["alerts"]
    assert (alert["kind"], alert["severity"]) == ("errors", severity)
    assert str(errors) in alert["message"]


def test_no_errors_no_alert(enrolled, admin_client, app):
    error_day(app, enrolled["pc_id"], 0)
    assert alerts(admin_client)["alerts"] == []


def test_a_revoked_key_is_noted(enrolled, admin_client):
    admin_client.delete(f"/admin/pcs/{enrolled['pc_id']}/key")
    assert kinds(admin_client) == [("revoked", "info")]


def test_a_station_added_long_ago_that_never_connected_is_noted_a_new_one_is_not(admin_client, app):
    fresh = admin_client.post("/admin/pcs", json={"display_name": "Fresh"}).json()
    old = admin_client.post("/admin/pcs", json={"display_name": "Forgotten"}).json()
    with app.state.session_factory() as db:
        db.get(Pc, old["pc_id"]).created_at = ago(days=9)
        db.commit()
    found = alerts(admin_client)["alerts"]
    assert [(a["kind"], a["pc_name"]) for a in found] == [("not_enrolled", "Forgotten")]
    assert fresh["pc_id"] not in [a["pc_id"] for a in found]


# -- ordering, counts, access ---------------------------------------------------------------------------------------
def test_alerts_are_listed_most_serious_first_and_counted(enrolled, admin_client, app):
    error_day(app, enrolled["pc_id"], 2)                                    # info
    other = admin_client.post("/admin/pcs", json={"display_name": "Quiet"}).json()
    reg = TestClient(app).post("/pcs/register", json={"code": other["enrollment_code"]})
    assert reg.status_code == 200
    last_seen(app, other["pc_id"], hours=30)                                # critical
    body = alerts(admin_client)
    assert [a["severity"] for a in body["alerts"]] == ["critical", "info"]
    assert body["counts"] == {"critical": 1, "warn": 0, "info": 1}


def test_alerts_need_an_admin_session_and_an_auditor_may_read_them(app, client):
    assert client.get("/admin/alerts").status_code == 401
    create_admin(app.state.session_factory, "watcher", "another long passphrase", role="auditor")
    r = client.post("/admin/login", json={"username": "watcher", "password": "another long passphrase"})
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert client.get("/admin/alerts").status_code == 200
