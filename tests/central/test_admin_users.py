"""Admin-user management: create, list, disable/enable, unlock, password
reset, own password change, session revocation."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from central.models import AdminUser, AuditEvent, utcnow

GOOD = "a very long passphrase"


def login(app, username, password):
    c = TestClient(app)
    r = c.post("/admin/login", json={"username": username, "password": password})
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c, r


def make_user(admin_client, username="auditor", password=GOOD):
    r = admin_client.post("/admin/users", json={"username": username,
                                                "password": password})
    assert r.status_code == 201, r.text
    return r.json()


def audit_actions(app):
    with app.state.session_factory() as db:
        return [e.action for e in db.scalars(select(AuditEvent))]


# -- create / list ---------------------------------------------------------------
def test_create_then_list_never_shows_hashes(admin_client):
    created = make_user(admin_client)
    assert created["username"] == "auditor" and created["status"] == "active"
    rows = admin_client.get("/admin/users").json()
    assert [r["username"] for r in rows] == ["auditor", "root"]
    assert all(set(r) == {"id", "username", "role", "status", "failed_count",
                          "locked_until"} for r in rows)


def test_a_created_admin_can_log_in(app, admin_client):
    make_user(admin_client)
    _client, r = login(app, "auditor", GOOD)
    assert r.status_code == 200


@pytest.mark.parametrize("body", [
    {"username": "ab", "password": GOOD},                  # too short
    {"username": "has space", "password": GOOD},
    {"username": "<script>", "password": GOOD},
    {"username": "ok-name", "password": "short"},           # under 12 characters
    {"username": "auditor-account", "password": "Auditor-Account"},  # same as username
    {"username": "ok-name", "password": "x" * 201},
])
def test_create_validates_input(admin_client, body):
    assert admin_client.post("/admin/users", json=body).status_code == 422


def test_duplicate_username_is_a_conflict(admin_client):
    make_user(admin_client)
    r = admin_client.post("/admin/users", json={"username": "auditor",
                                                "password": GOOD})
    assert r.status_code == 409


def test_creating_requires_login_and_csrf(client, admin_client):
    anon = TestClient(client.app)
    assert anon.post("/admin/users", json={"username": "abc",
                                           "password": GOOD}).status_code == 401
    del admin_client.headers["X-CSRF-Token"]
    assert admin_client.post("/admin/users", json={"username": "abc",
                                                   "password": GOOD}).status_code == 403


# -- disable / enable ----------------------------------------------------------------
def test_a_disabled_admin_cannot_log_in_and_is_logged_out(app, admin_client):
    user = make_user(admin_client)
    session, _ = login(app, "auditor", GOOD)
    assert session.get("/admin/me").status_code == 200
    r = admin_client.post(f"/admin/users/{user['id']}/disable")
    assert r.status_code == 200 and r.json()["status"] == "disabled"
    assert session.get("/admin/me").status_code == 401          # session revoked
    assert login(app, "auditor", GOOD)[1].status_code == 401     # looks like bad login


def test_enabling_restores_access(app, admin_client):
    user = make_user(admin_client)
    admin_client.post(f"/admin/users/{user['id']}/disable")
    r = admin_client.post(f"/admin/users/{user['id']}/enable")
    assert r.json()["status"] == "active"
    assert login(app, "auditor", GOOD)[1].status_code == 200


def test_you_cannot_disable_yourself(admin_client):
    me = next(u for u in admin_client.get("/admin/users").json()
              if u["username"] == "root")
    r = admin_client.post(f"/admin/users/{me['id']}/disable")
    assert r.status_code == 409 and "yourself" in r.json()["detail"]


def test_an_admin_can_disable_another_admin_who_then_cannot_act(app, admin_client):
    other = make_user(admin_client)
    other_client, _ = login(app, "auditor", GOOD)
    root = next(u for u in admin_client.get("/admin/users").json()
                if u["username"] == "root")
    assert other_client.post(f"/admin/users/{root['id']}/disable").status_code == 200
    assert admin_client.get("/admin/me").status_code == 401     # root is out
    assert other_client.get("/admin/users").status_code == 200  # auditor carries on
    assert other["status"] == "active"


def test_active_admin_count_ignores_disabled_accounts_and_the_excluded_one(app, admin_client):
    from central.routes_admin_users import active_admin_count
    other = make_user(admin_client)
    root = next(u for u in admin_client.get("/admin/users").json()
                if u["username"] == "root")
    with app.state.session_factory() as db:
        assert active_admin_count(db, excluding_id=root["id"]) == 1
        assert active_admin_count(db, excluding_id=other["id"]) == 1
        assert active_admin_count(db) == 2
    admin_client.post(f"/admin/users/{other['id']}/disable")
    with app.state.session_factory() as db:
        assert active_admin_count(db, excluding_id=root["id"]) == 0


def test_unknown_user_is_404(admin_client):
    assert admin_client.post("/admin/users/9999/disable").status_code == 404
    assert admin_client.post("/admin/users/9999/enable").status_code == 404
    assert admin_client.post("/admin/users/9999/password",
                             json={"password": GOOD}).status_code == 404


# -- unlock ----------------------------------------------------------------------------
def test_unlock_clears_a_lockout(app, admin_client):
    user = make_user(admin_client)
    with app.state.session_factory() as db:
        row = db.get(AdminUser, user["id"])
        row.locked_until = utcnow() + timedelta(minutes=30)
        row.failed_count = 3
        db.commit()
    listed = next(u for u in admin_client.get("/admin/users").json()
                  if u["id"] == user["id"])
    assert listed["status"] == "locked" and listed["failed_count"] == 3
    r = admin_client.post(f"/admin/users/{user['id']}/unlock")
    assert r.json()["status"] == "active" and r.json()["failed_count"] == 0
    assert login(app, "auditor", GOOD)[1].status_code == 200


def test_unlock_does_not_enable_a_disabled_account(admin_client):
    user = make_user(admin_client)
    admin_client.post(f"/admin/users/{user['id']}/disable")
    r = admin_client.post(f"/admin/users/{user['id']}/unlock")
    assert r.status_code == 409 and "disabled" in r.json()["detail"]


# -- password reset ------------------------------------------------------------------------
def test_reset_changes_the_password_and_revokes_that_users_sessions(app, admin_client):
    user = make_user(admin_client)
    session, _ = login(app, "auditor", GOOD)
    new = "a completely new passphrase"
    r = admin_client.post(f"/admin/users/{user['id']}/password",
                          json={"password": new})
    assert r.status_code == 204
    assert session.get("/admin/me").status_code == 401
    assert login(app, "auditor", GOOD)[1].status_code == 401
    assert login(app, "auditor", new)[1].status_code == 200


def test_reset_clears_a_lockout_but_not_a_disable(app, admin_client):
    user = make_user(admin_client)
    with app.state.session_factory() as db:
        db.get(AdminUser, user["id"]).locked_until = utcnow() + timedelta(minutes=30)
        db.commit()
    admin_client.post(f"/admin/users/{user['id']}/password", json={"password": GOOD + "!"})
    assert login(app, "auditor", GOOD + "!")[1].status_code == 200
    admin_client.post(f"/admin/users/{user['id']}/disable")
    admin_client.post(f"/admin/users/{user['id']}/password", json={"password": GOOD + "?"})
    assert login(app, "auditor", GOOD + "?")[1].status_code == 401


def test_reset_enforces_the_password_rules(admin_client):
    user = make_user(admin_client, username="auditor-account")
    assert admin_client.post(f"/admin/users/{user['id']}/password",
                             json={"password": "short"}).status_code == 422
    assert admin_client.post(f"/admin/users/{user['id']}/password",
                             json={"password": "AUDITOR-ACCOUNT"}).status_code == 422


# -- own password -----------------------------------------------------------------------------
def test_change_own_password_needs_the_current_one(app, admin_client):
    r = admin_client.post("/admin/me/password",
                          json={"current_password": "wrong password!!",
                                "new_password": "a brand new passphrase"})
    assert r.status_code == 403
    ok = admin_client.post("/admin/me/password",
                           json={"current_password": "correct horse battery",
                                 "new_password": "a brand new passphrase"})
    assert ok.status_code == 204
    assert admin_client.get("/admin/me").status_code == 401     # re-login required
    assert login(app, "root", "a brand new passphrase")[1].status_code == 200
    assert login(app, "root", "correct horse battery")[1].status_code == 401


# -- session revocation --------------------------------------------------------------------------
def test_revoke_one_users_sessions(app, admin_client):
    user = make_user(admin_client)
    session, _ = login(app, "auditor", GOOD)
    assert admin_client.post(
        f"/admin/users/{user['id']}/revoke-sessions").status_code == 204
    assert session.get("/admin/me").status_code == 401
    assert admin_client.get("/admin/me").status_code == 200      # root untouched
    assert login(app, "auditor", GOOD)[1].status_code == 200     # may sign in again


def test_revoke_all_sessions_logs_everyone_out_including_the_caller(app, admin_client):
    make_user(admin_client)
    session, _ = login(app, "auditor", GOOD)
    assert admin_client.post("/admin/users/revoke-all-sessions").status_code == 204
    assert session.get("/admin/me").status_code == 401
    assert admin_client.get("/admin/me").status_code == 401


# -- audit ----------------------------------------------------------------------------------------
def test_every_change_is_audited_without_secrets(app, admin_client):
    user = make_user(admin_client)
    admin_client.post(f"/admin/users/{user['id']}/password",
                      json={"password": "S3CRET-VALUE-123 passphrase"})
    admin_client.post(f"/admin/users/{user['id']}/disable")
    admin_client.post(f"/admin/users/{user['id']}/enable")
    admin_client.post(f"/admin/users/{user['id']}/revoke-sessions")
    actions = audit_actions(app)
    for expected in ("admin_user_created", "admin_password_reset",
                     "admin_user_disabled", "admin_user_enabled",
                     "admin_sessions_revoked"):
        assert expected in actions
    with app.state.session_factory() as db:
        text = " ".join(e.detail for e in db.scalars(select(AuditEvent)))
    assert "S3CRET-VALUE-123" not in text and GOOD not in text
