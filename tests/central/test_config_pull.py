import copy
import json
import uuid

from fastapi.testclient import TestClient

from central.models import AuditEvent, Pc
from tests.central.test_pcs import auth


def _publish(admin_client, project_config, name="Invoices"):
    p = str(uuid.uuid4())
    admin_client.put(f"/admin/projects/{p}",
                     json={"name": name, "config": project_config})
    return p


def _pull(e, **headers):
    return e["client"].get(f"/pcs/{e['pc_id']}/config",
                           headers={**auth(e), **headers})


def _pull_audits(app):
    with app.state.session_factory() as db:
        return [json.loads(r.detail) for r in
                db.query(AuditEvent).filter_by(action="config_pull")
                .order_by(AuditEvent.id)]


def _clear_last_seen(app, pc_id):
    with app.state.session_factory() as db:
        db.get(Pc, pc_id).last_seen_at = None
        db.commit()


def _last_seen(app, pc_id):
    with app.state.session_factory() as db:
        return db.get(Pc, pc_id).last_seen_at


def test_pull_with_nothing_assigned_is_empty(enrolled):
    r = _pull(enrolled)
    assert r.status_code == 200
    assert r.json() == {"pc_id": enrolled["pc_id"],
                        "poll_interval_seconds": 600, "projects": []}


def test_pull_returns_assigned_project_with_version(enrolled, admin_client, project_config):
    p = _publish(admin_client, project_config)
    admin_client.post(f"/admin/projects/{p}/push", json={"pc_ids": [enrolled["pc_id"]]})
    body = _pull(enrolled).json()
    assert len(body["projects"]) == 1
    proj = body["projects"][0]
    assert proj["project_id"] == p and proj["version"] == 1 and proj["name"] == "Invoices"
    assert proj["config"]["project_id"] == p


def test_pull_response_has_only_the_contract_keys(enrolled, admin_client, project_config):
    p = _publish(admin_client, project_config)
    admin_client.post(f"/admin/projects/{p}/push", json={"pc_ids": [enrolled["pc_id"]]})
    body = _pull(enrolled).json()
    assert set(body) == {"pc_id", "poll_interval_seconds", "projects"}
    assert set(body["projects"][0]) == {"project_id", "name", "version", "config"}


def test_pull_sees_new_version_after_admin_edit(enrolled, admin_client, project_config):
    p = _publish(admin_client, project_config)
    admin_client.post(f"/admin/projects/{p}/push", json={"pc_ids": [enrolled["pc_id"]]})
    edited = copy.deepcopy(project_config)
    edited["ocr"]["lang"] = "deu"
    admin_client.put(f"/admin/projects/{p}", json={"name": "Invoices", "config": edited})
    proj = _pull(enrolled).json()["projects"][0]
    assert proj["version"] == 2 and proj["config"]["ocr"]["lang"] == "deu"


def test_unassigned_project_disappears_from_next_pull(enrolled, admin_client, project_config):
    keep = _publish(admin_client, project_config, name="Keep")
    drop = _publish(admin_client, project_config, name="Drop")
    for p in (keep, drop):
        admin_client.post(f"/admin/projects/{p}/push", json={"pc_ids": [enrolled["pc_id"]]})
    assert {x["project_id"] for x in _pull(enrolled).json()["projects"]} == {keep, drop}
    assert admin_client.delete(
        f"/admin/projects/{drop}/assignments/{enrolled['pc_id']}").status_code == 204
    assert [x["project_id"] for x in _pull(enrolled).json()["projects"]] == [keep]


def test_pull_honours_poll_interval_setting(enrolled, admin_client):
    admin_client.put("/admin/settings", json={"poll_interval_seconds": 90})
    assert _pull(enrolled).json()["poll_interval_seconds"] == 90


def test_pc_never_sees_projects_assigned_to_other_pcs(enrolled, admin_client, project_config, app):
    other = admin_client.post("/admin/pcs", json={"display_name": "PC-2"}).json()
    TestClient(app).post("/pcs/register", json={"code": other["enrollment_code"]})
    p = _publish(admin_client, project_config)
    admin_client.post(f"/admin/projects/{p}/push", json={"pc_ids": [other["pc_id"]]})
    assert _pull(enrolled).json()["projects"] == []


def test_pull_needs_valid_key_for_that_pc(enrolled, admin_client):
    url = f"/pcs/{enrolled['pc_id']}/config"
    assert enrolled["client"].get(url).status_code == 401
    assert enrolled["client"].get(url, headers=auth(enrolled, "dsk_bad")).status_code == 401
    admin_client.delete(f"/admin/pcs/{enrolled['pc_id']}/key")
    assert enrolled["client"].get(url, headers=auth(enrolled)).status_code == 401


def test_pull_refreshes_last_seen(enrolled, app):
    _clear_last_seen(app, enrolled["pc_id"])
    _pull(enrolled)
    assert _last_seen(app, enrolled["pc_id"]) is not None


def test_each_pull_writes_one_config_pull_audit_row_with_served_versions(
        enrolled, admin_client, project_config, app):
    p = _publish(admin_client, project_config)
    admin_client.post(f"/admin/projects/{p}/push", json={"pc_ids": [enrolled["pc_id"]]})
    _pull(enrolled)
    rows = _pull_audits(app)
    assert len(rows) == 1 and rows[0]["projects"] == {p: 1}
    _pull(enrolled)
    assert len(_pull_audits(app)) == 2


def test_operator_header_is_audited_and_capped(enrolled, app):
    _pull(enrolled, **{"X-Operator-Id": "alice"})
    _pull(enrolled, **{"X-Operator-Id": "x" * 500})
    rows = _pull_audits(app)
    assert rows[0]["operator"] == "alice"
    assert len(rows[1]["operator"]) == 100


def test_non_ascii_operator_is_stored_unmangled_by_sanitizer(enrolled, app):
    # the test client sends the header bytes as given (UTF-8) and Starlette decodes header
    # bytes as latin-1, so the stored text is that latin-1 view of the UTF-8
    # bytes. The sanitizer must keep every character (no '?', no 500).
    r = _pull(enrolled, **{"X-Operator-Id": "Zoë".encode("utf-8")})
    assert r.status_code == 200
    stored = _pull_audits(app)[0]["operator"]
    assert "?" not in stored
    assert stored.encode("latin-1").decode("utf-8") == "Zoë"


def test_absent_operator_header_is_stored_empty(enrolled, app):
    _pull(enrolled)
    assert _pull_audits(app)[0]["operator"] == ""


def test_rejected_pulls_write_no_audit_and_do_not_touch_last_seen(
        enrolled, admin_client, app):
    other = admin_client.post("/admin/pcs", json={"display_name": "PC-2"}).json()
    other_reg = TestClient(app).post("/pcs/register",
                                     json={"code": other["enrollment_code"]})
    other_key = other_reg.json()["api_key"]
    for pc_id in (enrolled["pc_id"], other["pc_id"]):
        _clear_last_seen(app, pc_id)
    url = f"/pcs/{enrolled['pc_id']}/config"
    c = enrolled["client"]
    assert c.get(url, headers=auth(enrolled, "dsk_bad")).status_code == 401
    assert c.get(url, headers=auth(enrolled, other_key)).status_code == 403
    assert _pull_audits(app) == []
    assert _last_seen(app, enrolled["pc_id"]) is None
    assert _last_seen(app, other["pc_id"]) is None
