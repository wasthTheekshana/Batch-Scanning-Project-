"""A read-only auditor account: sees everything an admin sees, changes nothing."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from central.models import AdminUser, AuditEvent
from central.routes_admin_users import active_admin_count
from tests.central.test_route_guards import _admin_requests

GOOD = "a very long passphrase"


def sign_in(app, username, password=GOOD):
    c = TestClient(app)
    r = c.post("/admin/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c, r.json()


@pytest.fixture()
def auditor(admin_client, app):
    r = admin_client.post("/admin/users", json={"username": "auditor1", "password": GOOD,
                                                "role": "auditor"})
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "auditor"
    client, me = sign_in(app, "auditor1")
    assert me["role"] == "auditor"
    return client


def actions(app):
    with app.state.session_factory() as db:
        return [e.action for e in db.scalars(select(AuditEvent))]


def test_the_default_role_is_still_admin(admin_client):
    r = admin_client.post("/admin/users", json={"username": "second", "password": GOOD})
    assert r.status_code == 201 and r.json()["role"] == "admin"


def test_an_unknown_role_is_refused(admin_client):
    r = admin_client.post("/admin/users", json={"username": "x1x", "password": GOOD,
                                                "role": "superuser"})
    assert r.status_code == 422


def test_an_auditor_can_read_everything(auditor):
    for url in ("/admin/overview", "/admin/users", "/admin/pcs", "/admin/projects",
                "/admin/templates", "/admin/settings", "/admin/audit/actions", "/admin/me"):
        assert auditor.get(url).status_code == 200, url
    assert auditor.get("/admin/audit").status_code in (200, 422)     # needs no write right


def test_an_auditor_cannot_change_anything(auditor, app):
    """Every mutating admin route, except the auditor's own sign-out and password."""
    own = {"/admin/logout", "/admin/me/password"}
    refused = 0
    for method, url in _admin_requests(app):
        if method == "GET" or url in own:
            continue
        r = auditor.request(method, url, json={})
        assert r.status_code == 403 and "read-only" in r.text, (method, url, r.status_code)
        refused += 1
    assert refused >= 9


def test_an_auditor_cannot_create_a_project_or_a_pc(auditor, project_config):
    assert auditor.post("/admin/pcs", json={"display_name": "PC-9"}).status_code == 403
    assert auditor.put("/admin/projects/" + project_config["project_id"],
                       json={"name": "x", "config": project_config}).status_code == 403
    assert auditor.post("/admin/users", json={"username": "evil1", "password": GOOD,
                                              "role": "admin"}).status_code == 403


def test_an_auditor_can_change_their_own_password_and_sign_out(auditor, app):
    r = auditor.post("/admin/me/password",
                     json={"current_password": GOOD, "new_password": "another long passphrase"})
    assert r.status_code == 204
    client, _me = sign_in(app, "auditor1", "another long passphrase")
    assert client.post("/admin/logout").status_code == 204


def test_an_admin_can_change_a_role_and_the_change_is_audited(admin_client, app, auditor):
    uid = next(u["id"] for u in admin_client.get("/admin/users").json() if u["username"] == "auditor1")
    r = admin_client.post(f"/admin/users/{uid}/role", json={"role": "admin"})
    assert r.status_code == 200 and r.json()["role"] == "admin"
    assert "admin_role_changed" in actions(app)
    # their old session no longer counts (rights changed): they sign in again
    assert auditor.get("/admin/overview").status_code == 401
    new, me = sign_in(app, "auditor1")
    assert me["role"] == "admin" and new.post("/admin/pcs", json={"display_name": "P"}).status_code in (200, 201)


def test_an_admin_cannot_change_their_own_role(admin_client):
    me = next(u for u in admin_client.get("/admin/users").json() if u["username"] == "root")
    r = admin_client.post(f"/admin/users/{me['id']}/role", json={"role": "auditor"})
    assert r.status_code == 409


def test_an_unknown_role_on_change_is_refused(admin_client, auditor):
    uid = next(u["id"] for u in admin_client.get("/admin/users").json() if u["username"] == "auditor1")
    assert admin_client.post(f"/admin/users/{uid}/role", json={"role": "root"}).status_code == 422


def test_the_last_admin_cannot_be_demoted_and_auditors_do_not_count_as_admins(admin_client, app):
    r = admin_client.post("/admin/users", json={"username": "second", "password": GOOD})
    second = r.json()["id"]
    second_client, _ = sign_in(app, "second")
    me = next(u for u in admin_client.get("/admin/users").json() if u["username"] == "root")
    # root demotes second: root stays an admin, fine
    assert admin_client.post(f"/admin/users/{second}/role", json={"role": "auditor"}).status_code == 200
    with app.state.session_factory() as db:
        assert active_admin_count(db) == 1                 # the auditor is not an admin
        assert active_admin_count(db, excluding_id=me["id"]) == 0
    # the only admin left cannot be disabled or demoted by anyone who could try
    assert admin_client.post(f"/admin/users/{me['id']}/disable").status_code == 409


def test_a_disabled_auditor_cannot_sign_in(admin_client, app, auditor):
    uid = next(u["id"] for u in admin_client.get("/admin/users").json() if u["username"] == "auditor1")
    assert admin_client.post(f"/admin/users/{uid}/disable").status_code == 200
    c = TestClient(app)
    assert c.post("/admin/login", json={"username": "auditor1", "password": GOOD}).status_code == 401


def test_the_cli_can_create_an_auditor(app):
    from central.manage import create_admin
    create_admin(app.state.session_factory, "audit.cli", "correct horse battery", role="auditor")
    with app.state.session_factory() as db:
        assert db.scalar(select(AdminUser.role).where(AdminUser.username == "audit.cli")) == "auditor"
