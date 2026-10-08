"""Admin PC management endpoints."""
import json

from central.models import EnrollmentCode, Pc


def test_pc_endpoints_require_login(client):
    assert client.post("/admin/pcs", json={"display_name": "x"}).status_code == 401
    assert client.get("/admin/pcs").status_code == 401


def test_create_pc_returns_one_time_code_and_lists_unregistered(admin_client):
    r = admin_client.post("/admin/pcs", json={"display_name": "Scan-01"})
    assert r.status_code == 201
    body = r.json()
    assert body["display_name"] == "Scan-01"
    assert len(body["enrollment_code"]) >= 16 and body["pc_id"]
    assert "api_key" not in body

    rows = admin_client.get("/admin/pcs").json()
    assert [(p["display_name"], p["registered"]) for p in rows] == [
        ("Scan-01", False)]
    assert "enrollment_code" not in rows[0] and "api_key_hash" not in rows[0]


def test_duplicate_display_name_conflicts(admin_client):
    admin_client.post("/admin/pcs", json={"display_name": "Scan-01"})
    r = admin_client.post("/admin/pcs", json={"display_name": "Scan-01"})
    assert r.status_code == 409


def test_blank_display_name_rejected(admin_client):
    assert admin_client.post("/admin/pcs",
                             json={"display_name": ""}).status_code == 422


def test_new_enrollment_code_replaces_the_old_one(admin_client, app):
    d = admin_client.post("/admin/pcs", json={"display_name": "A"}).json()
    r = admin_client.post(f"/admin/pcs/{d['pc_id']}/enrollment-code")
    assert r.status_code == 200
    assert r.json()["enrollment_code"] != d["enrollment_code"]
    with app.state.session_factory() as db:
        assert db.query(EnrollmentCode).filter_by(pc_id=d["pc_id"]).count() == 1


def test_enrollment_code_for_unknown_pc_is_404(admin_client):
    assert admin_client.post("/admin/pcs/nope/enrollment-code").status_code == 404


def test_revoke_key_clears_hash_and_404s_for_unknown(admin_client, app):
    d = admin_client.post("/admin/pcs", json={"display_name": "A"}).json()
    with app.state.session_factory() as db:
        db.get(Pc, d["pc_id"]).api_key_hash = "h" * 64
        db.commit()
    assert admin_client.delete(f"/admin/pcs/{d['pc_id']}/key").status_code == 204
    with app.state.session_factory() as db:
        assert db.get(Pc, d["pc_id"]).api_key_hash is None
    assert admin_client.delete("/admin/pcs/nope/key").status_code == 404


def test_mutations_need_csrf(client, admin):
    client.post("/admin/login", json={
        "username": "root", "password": "correct horse battery"})
    r = client.post("/admin/pcs", json={"display_name": "x"})  # no CSRF header
    assert r.status_code == 403


def test_post_pc_with_lone_surrogate_returns_422(client, admin):
    """Endpoint test: POST /admin/pcs with lone surrogate in display_name returns 422."""
    # First, log in to get a valid admin session
    r_login = client.post("/admin/login", json={
        "username": "root", "password": "correct horse battery"})
    assert r_login.status_code == 200
    csrf = r_login.json()["csrf_token"]
    client.headers["X-CSRF-Token"] = csrf

    # Send raw JSON with lone surrogate
    body = json.dumps({"display_name": "\ud800"})
    r = client.post(
        "/admin/pcs",
        content=body.encode("utf-8"),
        headers={"content-type": "application/json"}
    )
    assert r.status_code == 422


def test_create_pc_duplicate_at_insert_time_returns_409(admin_client, app, monkeypatch):
    """Test that duplicate name caught at insert time (race condition) returns 409, not 500.

    Simulates concurrent creates: both threads pass the select check, but the second
    thread hits the UNIQUE constraint at insert time.
    """
    from sqlalchemy.orm import Session
    from central.models import utcnow
    from uuid import uuid4

    # Pre-insert a PC with the target name
    with app.state.session_factory() as db:
        pc = Pc(id=str(uuid4()), display_name="RaceTest", created_at=utcnow())
        db.add(pc)
        db.commit()

    # Patch Session.scalar to return None for the first call (the existence check)
    # This simulates the race condition where both threads see the name as non-existent
    original_scalar = Session.scalar
    call_count = [0]

    def patched_scalar(self, stmt):
        call_count[0] += 1
        # Return None for the first scalar call (the duplicate check)
        if call_count[0] == 1:
            call_count[0] = 0  # Reset for next request
            return None
        return original_scalar(self, stmt)

    monkeypatch.setattr(Session, "scalar", patched_scalar)

    # Try to create another PC with the same name
    # The pre-check will pass (scalar returns None), but insert will fail with IntegrityError
    r = admin_client.post("/admin/pcs", json={"display_name": "RaceTest"})
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]

    # Verify exactly one PC with that name exists in the database
    with app.state.session_factory() as db:
        count = db.query(Pc).filter_by(display_name="RaceTest").count()
        assert count == 1


def test_revoke_key_also_kills_pending_enrollment_code(admin_client, app, client):
    d = admin_client.post("/admin/pcs", json={"display_name": "Gone"}).json()
    assert admin_client.delete(f"/admin/pcs/{d['pc_id']}/key").status_code == 204
    r = client.post("/pcs/register", json={"code": d["enrollment_code"]})
    assert r.status_code == 400
    with app.state.session_factory() as db:
        assert db.get(Pc, d["pc_id"]).api_key_hash is None
        assert db.query(EnrollmentCode).filter_by(pc_id=d["pc_id"]).count() == 0
