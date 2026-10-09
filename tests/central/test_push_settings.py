import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from central.models import AuditEvent, PcAssignment


def _project(c, project_config):
    p = str(uuid.uuid4())
    r = c.put(f"/admin/projects/{p}",
              json={"name": "P", "config": project_config})
    assert r.status_code == 200
    return p


def _pc(c, name):
    return c.post("/admin/pcs", json={"display_name": name}).json()["pc_id"]


def _assigned(c):
    return {p["id"]: p["assigned_project_ids"]
            for p in c.get("/admin/pcs").json()}


def _assignment_count(app, project_id, pc_id):
    with app.state.session_factory() as s:
        return s.scalar(select(func.count()).select_from(PcAssignment).where(
            PcAssignment.project_id == project_id,
            PcAssignment.pc_id == pc_id))


def test_push_to_selected_pcs(admin_client, project_config):
    p, a, b = _project(admin_client, project_config), _pc(admin_client, "A"), _pc(admin_client, "B")
    r = admin_client.post(f"/admin/projects/{p}/push", json={"pc_ids": [a]})
    assert r.status_code == 200 and r.json() == {"assigned": [a]}
    assert _assigned(admin_client) == {a: [p], b: []}


def test_push_to_all_pcs_is_idempotent(admin_client, project_config):
    p, a, b = _project(admin_client, project_config), _pc(admin_client, "A"), _pc(admin_client, "B")
    for _ in range(2):
        r = admin_client.post(f"/admin/projects/{p}/push", json={"all_pcs": True})
        assert r.status_code == 200 and sorted(r.json()["assigned"]) == sorted([a, b])
    assert _assigned(admin_client) == {a: [p], b: [p]}


def test_pc_enrolled_after_push_to_all_gets_nothing(admin_client, project_config):
    p = _project(admin_client, project_config)
    _pc(admin_client, "A")
    admin_client.post(f"/admin/projects/{p}/push", json={"all_pcs": True})
    late = _pc(admin_client, "Late")
    assert _assigned(admin_client)[late] == []


def test_push_needs_exactly_one_mode(admin_client, project_config):
    p, a = _project(admin_client, project_config), _pc(admin_client, "A")
    url = f"/admin/projects/{p}/push"
    assert admin_client.post(url, json={}).status_code == 422
    assert admin_client.post(url, json={"pc_ids": [a], "all_pcs": True}).status_code == 422


def test_push_with_no_pcs_registered_is_an_empty_success(admin_client, project_config):
    p = _project(admin_client, project_config)
    r = admin_client.post(f"/admin/projects/{p}/push", json={"all_pcs": True})
    assert r.status_code == 200 and r.json() == {"assigned": []}


def test_push_unknown_pc_or_project_is_404(admin_client, project_config):
    p, a = _project(admin_client, project_config), _pc(admin_client, "A")
    assert admin_client.post(f"/admin/projects/{p}/push",
                             json={"pc_ids": [a, "ghost"]}).status_code == 404
    assert _assigned(admin_client)[a] == []  # nothing half-applied
    assert admin_client.post("/admin/projects/nope/push",
                             json={"all_pcs": True}).status_code == 404


def test_push_duplicate_pc_ids_assign_once(admin_client, project_config, app):
    p, a = _project(admin_client, project_config), _pc(admin_client, "A")
    r = admin_client.post(f"/admin/projects/{p}/push", json={"pc_ids": [a, a]})
    assert r.status_code == 200 and r.json() == {"assigned": [a]}
    assert _assignment_count(app, p, a) == 1


def test_concurrent_push_of_same_assignment_is_idempotent_success(
        admin_client, project_config, monkeypatch, app):
    p, a = _project(admin_client, project_config), _pc(admin_client, "A")
    real_scalars = Session.scalars
    state = {"fired": False}

    def racing_scalars(self, statement, *args, **kw):
        result = real_scalars(self, statement, *args, **kw)
        # fire right after the route reads the existing assignments
        if "pc_assignments" in str(statement) and not state["fired"]:
            state["fired"] = True
            with app.state.session_factory() as other:
                other.add(PcAssignment(pc_id=a, project_id=p))
                other.commit()
        return result

    monkeypatch.setattr(Session, "scalars", racing_scalars)
    r = admin_client.post(f"/admin/projects/{p}/push", json={"pc_ids": [a]})
    assert state["fired"]
    assert r.status_code == 200 and r.json() == {"assigned": [a]}
    assert _assignment_count(app, p, a) == 1


def test_concurrent_push_overlap_still_assigns_every_requested_pc(
        admin_client, project_config, monkeypatch, app):
    """I2: a competing insert of (a, p) must not discard the pending (b, p)."""
    p = _project(admin_client, project_config)
    a, b = _pc(admin_client, "A"), _pc(admin_client, "B")
    real_scalars = Session.scalars
    state = {"fired": False}

    def racing_scalars(self, statement, *args, **kw):
        result = real_scalars(self, statement, *args, **kw)
        if "pc_assignments" in str(statement) and not state["fired"]:
            state["fired"] = True
            with app.state.session_factory() as other:
                other.add(PcAssignment(pc_id=a, project_id=p))
                other.commit()
        return result

    monkeypatch.setattr(Session, "scalars", racing_scalars)
    r = admin_client.post(f"/admin/projects/{p}/push", json={"pc_ids": [a, b]})
    assert state["fired"]
    assert r.status_code == 200 and sorted(r.json()["assigned"]) == sorted([a, b])
    assert _assignment_count(app, p, a) == 1
    assert _assignment_count(app, p, b) == 1
    with app.state.session_factory() as s:
        pushed = [json.loads(e.detail) for e in s.scalars(
            select(AuditEvent).where(AuditEvent.action == "project_pushed"))]
    assert len(pushed) == 1 and sorted(pushed[0]["pc_ids"]) == sorted([a, b])


def test_unassign(admin_client, project_config):
    p, a = _project(admin_client, project_config), _pc(admin_client, "A")
    admin_client.post(f"/admin/projects/{p}/push", json={"pc_ids": [a]})
    assert admin_client.delete(f"/admin/projects/{p}/assignments/{a}").status_code == 204
    assert _assigned(admin_client)[a] == []
    assert admin_client.delete(f"/admin/projects/{p}/assignments/{a}").status_code == 404


def test_unassign_unknown_project_is_404(admin_client):
    a = _pc(admin_client, "A")
    assert admin_client.delete(
        f"/admin/projects/{uuid.uuid4()}/assignments/{a}").status_code == 404


def test_unassign_unknown_assignment_is_404_and_keeps_others(admin_client, project_config):
    p, a, b = _project(admin_client, project_config), _pc(admin_client, "A"), _pc(admin_client, "B")
    admin_client.post(f"/admin/projects/{p}/push", json={"pc_ids": [a]})
    assert admin_client.delete(f"/admin/projects/{p}/assignments/{b}").status_code == 404
    assert admin_client.delete(f"/admin/projects/{p}/assignments/ghost").status_code == 404
    assert _assigned(admin_client)[a] == [p]


def test_poll_interval_setting(admin_client):
    assert admin_client.get("/admin/settings").json() == {"poll_interval_seconds": 600}
    assert admin_client.put("/admin/settings",
                            json={"poll_interval_seconds": 120}).status_code == 200
    assert admin_client.get("/admin/settings").json()["poll_interval_seconds"] == 120
    assert admin_client.put("/admin/settings",
                            json={"poll_interval_seconds": 5}).status_code == 422
    assert admin_client.put("/admin/settings",
                            json={"poll_interval_seconds": 10**7}).status_code == 422
    assert admin_client.get("/admin/settings").json()["poll_interval_seconds"] == 120


@pytest.mark.parametrize("value", [30, 86400])
def test_poll_interval_boundaries_accepted(admin_client, value):
    r = admin_client.put("/admin/settings", json={"poll_interval_seconds": value})
    assert r.status_code == 200 and r.json() == {"poll_interval_seconds": value}
    assert admin_client.get("/admin/settings").json()["poll_interval_seconds"] == value


@pytest.mark.parametrize("value", [29, 86401, "abc", 12.5])
def test_poll_interval_out_of_range_or_non_integer_rejected(admin_client, value):
    r = admin_client.put("/admin/settings", json={"poll_interval_seconds": value})
    assert r.status_code == 422
    assert admin_client.get("/admin/settings").json()["poll_interval_seconds"] == 600


def test_registration_returns_the_configured_poll_interval(admin_client, app):
    assert admin_client.put("/admin/settings",
                            json={"poll_interval_seconds": 120}).status_code == 200
    code = admin_client.post("/admin/pcs",
                             json={"display_name": "New"}).json()["enrollment_code"]
    reg = TestClient(app).post("/pcs/register", json={"code": code})
    assert reg.status_code == 200
    assert reg.json()["poll_interval_seconds"] == 120
