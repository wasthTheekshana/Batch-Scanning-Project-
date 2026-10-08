import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from central.app import create_app
from central.manage import create_admin
from central.models import AdminUser

PW = "correct horse battery"


def _login(client, password=PW, username="root"):
    return client.post("/admin/login",
                       json={"username": username, "password": password})


def test_login_returns_csrf_token_and_me_works(client, admin):
    r = _login(client)
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "root" and len(body["csrf_token"]) >= 32
    me = client.get("/admin/me")
    assert me.status_code == 200 and me.json()["username"] == "root"


def test_me_requires_login(client):
    assert client.get("/admin/me").status_code == 401


def test_wrong_password_and_unknown_user_look_identical(client, admin):
    a = _login(client, "wrong password!")
    b = _login(client, "whatever", username="nobody")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_lockout_after_repeated_failures(client, admin):
    for _ in range(5):
        assert _login(client, "wrong password!").status_code == 401
    # even the correct password is refused while locked (401, same as unknown user)
    r_locked = _login(client)
    assert r_locked.status_code == 401
    # body should match unknown user response
    r_unknown = _login(client, "whatever", username="nobody")
    assert r_locked.json() == r_unknown.json()


def test_successful_login_resets_failure_count(client, admin):
    for _ in range(4):
        _login(client, "wrong password!")
    assert _login(client).status_code == 200
    for _ in range(4):
        _login(client, "wrong password!")
    assert _login(client).status_code == 200  # never reached 5 in a row


def test_logout_requires_csrf_and_ends_session(client, admin):
    token = _login(client).json()["csrf_token"]
    assert client.post("/admin/logout").status_code == 403
    r = client.post("/admin/logout", headers={"X-CSRF-Token": token})
    assert r.status_code == 204
    assert client.get("/admin/me").status_code == 401


def test_wrong_csrf_token_rejected(client, admin):
    _login(client)
    r = client.post("/admin/logout", headers={"X-CSRF-Token": "nope"})
    assert r.status_code == 403


def test_create_admin_enforces_password_length_and_uniqueness(app):
    factory = app.state.session_factory
    with pytest.raises(ValueError, match="at least 12"):
        create_admin(factory, "short", "tooshort")
    create_admin(factory, "dup", PW)
    with pytest.raises(ValueError, match="already exists"):
        create_admin(factory, "dup", PW)


def test_login_rejects_surrogate_in_password(client, admin):
    # Test posting a raw JSON body with a lone surrogate escape sequence
    # httpx's json= parameter may refuse to encode it, so we use a raw body
    import json
    body_with_surrogate = '{"username": "root", "password": "\\ud800"}'
    r = client.post(
        "/admin/login",
        content=body_with_surrogate.encode("utf-8"),
        headers={"content-type": "application/json"}
    )
    assert r.status_code == 422


def test_login_rejects_surrogate_in_username(client, admin):
    # Test posting a raw JSON body with a lone surrogate escape sequence
    import json
    body_with_surrogate = '{"username": "\\ud800", "password": "x"}'
    r = client.post(
        "/admin/login",
        content=body_with_surrogate.encode("utf-8"),
        headers={"content-type": "application/json"}
    )
    assert r.status_code == 422


def test_login_sets_secure_cookie_flags(client, admin):
    r = _login(client)
    assert r.status_code == 200
    # Check Set-Cookie header for dok_central cookie
    cookie_header = r.headers.get("set-cookie", "").lower()
    assert cookie_header.startswith("dok_central="), f"Cookie name not found in: {cookie_header}"
    assert "httponly" in cookie_header, f"HttpOnly not found in: {cookie_header}"
    assert "samesite=strict" in cookie_header, f"SameSite=strict not found in: {cookie_header}"


def test_login_missing_password_returns_422(client, admin):
    """Test that missing password field returns 422 and doesn't echo input"""
    body_missing = '{"username": "root"}'
    r = client.post(
        "/admin/login",
        content=body_missing.encode("utf-8"),
        headers={"content-type": "application/json"}
    )
    assert r.status_code == 422
    # Verify response doesn't include the input field
    assert "input" not in r.text


def test_login_surrogate_in_username_only_returns_422(client, admin):
    """F1 Regression: surrogate in username (no password field) returns 422, not 500"""
    # Raw JSON body with surrogate in username, password omitted
    body_with_surrogate = '{"username": "\\ud800"}'
    r = client.post(
        "/admin/login",
        content=body_with_surrogate.encode("utf-8"),
        headers={"content-type": "application/json"}
    )
    assert r.status_code == 422
    # Verify response doesn't include the input field (which would contain the surrogate)
    assert "input" not in r.text


def test_login_over_length_password_not_echoed(client, admin):
    """Test that over-length password (>200 chars) returns 422 and doesn't echo password"""
    long_password = "x" * 300
    body = '{"username": "root", "password": "' + long_password + '"}'
    r = client.post(
        "/admin/login",
        content=body.encode("utf-8"),
        headers={"content-type": "application/json"}
    )
    assert r.status_code == 422
    # Verify the password is not echoed in the response
    assert long_password not in r.text
    # Verify input field is not present
    assert "input" not in r.text


def test_session_invalidation_on_logout(app):
    """Test that a captured cookie becomes invalid after logout"""
    factory = app.state.session_factory
    create_admin(factory, "testuser", PW)

    # Login and capture the session cookie
    client1 = TestClient(app)
    r1 = client1.post("/admin/login", json={"username": "testuser", "password": PW})
    assert r1.status_code == 200
    old_cookie = client1.cookies.get("dok_central")
    assert old_cookie is not None

    # Logout with CSRF token
    token = r1.json()["csrf_token"]
    logout_r = client1.post("/admin/logout", headers={"X-CSRF-Token": token})
    assert logout_r.status_code == 204

    # Create a new client and set the old cookie
    client2 = TestClient(app)
    client2.cookies.set("dok_central", old_cookie)

    # The old cookie should now be invalid
    me_r = client2.get("/admin/me")
    assert me_r.status_code == 401


def test_session_per_user_independent(app):
    """Test that logout of one user doesn't invalidate other users' sessions"""
    factory = app.state.session_factory
    create_admin(factory, "user1", PW)
    create_admin(factory, "user2", PW)

    # User1 logs in
    c1 = TestClient(app)
    r1 = c1.post("/admin/login", json={"username": "user1", "password": PW})
    assert r1.status_code == 200
    token1 = r1.json()["csrf_token"]

    # User2 logs in
    c2 = TestClient(app)
    r2 = c2.post("/admin/login", json={"username": "user2", "password": PW})
    assert r2.status_code == 200

    # User1 logs out
    c1.post("/admin/logout", headers={"X-CSRF-Token": token1})

    # User1 should be logged out
    assert c1.get("/admin/me").status_code == 401

    # User2 should still be logged in
    assert c2.get("/admin/me").status_code == 200


def test_lockout_does_not_invalidate_other_sessions(app):
    """F6: Test that triggering lockout via bad logins doesn't invalidate valid sessions.
    Lockout should not bump session_version (that's logout's job only)."""
    factory = app.state.session_factory
    create_admin(factory, "testuser", PW)

    # Session A: User logs in successfully
    c_valid = TestClient(app)
    r_login = c_valid.post("/admin/login", json={"username": "testuser", "password": PW})
    assert r_login.status_code == 200
    # Session A should work
    assert c_valid.get("/admin/me").status_code == 200

    # Session B: Try to login 5 times with wrong password to trigger lockout
    c_attacker = TestClient(app)
    for _ in range(5):
        r = c_attacker.post("/admin/login", json={"username": "testuser", "password": "wrong"})
        assert r.status_code == 401

    # Session A should STILL work (lockout doesn't invalidate other sessions)
    assert c_valid.get("/admin/me").status_code == 200


def test_record_failed_login_atomic(app):
    """F3: Test that failed_count increments atomically even with concurrent access.

    Opens two sessions, loads the user in both (keeping objects alive with failed_count=0),
    then calls record_failed_login through both. With atomic UPDATE, final count is 2.
    With read-modify-write (+=), would race and give 1."""
    from central.routes_admin_auth import record_failed_login

    factory = app.state.session_factory
    create_admin(factory, "testuser", PW)

    # Open two separate sessions and load the user in BOTH
    # Keep the ORM objects alive to simulate concurrent transactions
    db1 = factory()
    db2 = factory()

    try:
        user1 = db1.scalar(select(AdminUser).where(AdminUser.username == "testuser"))
        user2 = db2.scalar(select(AdminUser).where(AdminUser.username == "testuser"))

        # Both see failed_count = 0
        assert user1.failed_count == 0
        assert user2.failed_count == 0
        user_id = user1.id

        # Now call record_failed_login through both sessions (simulating concurrent requests)
        record_failed_login(db1, user_id, 5, 15)
        record_failed_login(db2, user_id, 5, 15)
    finally:
        db1.close()
        db2.close()

    # Final count should be 2, not 1 (which would happen with read-modify-write race)
    with factory() as db:
        user = db.scalar(select(AdminUser).where(AdminUser.username == "testuser"))
        assert user.failed_count == 2


def test_lockout_decision_uses_db_value_not_stale_cached_object(app):
    """I1: the session already holds the user at failed_count=0 while another
    connection moved the DB count to 4; the 5th failure must lock."""
    from sqlalchemy import update

    from central.routes_admin_auth import record_failed_login

    factory = app.state.session_factory
    create_admin(factory, "testuser", PW)
    with factory() as db_a, factory() as db_b:
        cached = db_a.scalar(
            select(AdminUser).where(AdminUser.username == "testuser"))
        assert cached.failed_count == 0
        db_b.execute(update(AdminUser).where(AdminUser.id == cached.id)
                     .values(failed_count=4))
        db_b.commit()

        record_failed_login(db_a, cached.id, 5, 15)

    with factory() as db:
        fresh = db.scalar(
            select(AdminUser).where(AdminUser.username == "testuser"))
        assert fresh.locked_until is not None
        assert fresh.failed_count == 0
