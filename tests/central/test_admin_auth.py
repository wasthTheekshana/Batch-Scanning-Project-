import pytest

from central.manage import create_admin

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
    # even the correct password is refused while locked
    assert _login(client).status_code == 429


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
    assert "httponly" in cookie_header, f"HttpOnly not found in: {cookie_header}"
    assert "samesite=strict" in cookie_header, f"SameSite=strict not found in: {cookie_header}"
