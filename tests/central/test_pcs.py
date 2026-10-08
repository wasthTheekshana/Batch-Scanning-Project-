from datetime import timedelta

from central.models import EnrollmentCode, Pc, utcnow
from central.security import hash_secret


def auth(e, key=None):
    return {"Authorization": f"Bearer {key or e['api_key']}"}


def test_register_returns_key_and_poll_interval(enrolled, admin_client):
    assert enrolled["api_key"].startswith("dsk_")
    rows = admin_client.get("/admin/pcs").json()
    assert rows[0]["registered"] is True and rows[0]["last_seen_at"]


def test_register_response_has_poll_interval(admin_client, app):
    from fastapi.testclient import TestClient
    d = admin_client.post("/admin/pcs", json={"display_name": "P"}).json()
    r = TestClient(app).post("/pcs/register",
                             json={"code": d["enrollment_code"]})
    assert r.json()["poll_interval_seconds"] == 600


def test_code_is_single_use(enrolled):
    r = enrolled["client"].post("/pcs/register", json={"code": enrolled["code"]})
    assert r.status_code == 400


def test_unknown_code_is_rejected_like_a_used_one(enrolled):
    used = enrolled["client"].post("/pcs/register", json={"code": enrolled["code"]})
    bad = enrolled["client"].post("/pcs/register",
                                  json={"code": "does-not-exist-123"})
    assert bad.status_code == used.status_code == 400
    assert bad.json() == used.json()


def test_expired_code_is_rejected_and_mints_no_key(admin_client, app):
    from fastapi.testclient import TestClient
    d = admin_client.post("/admin/pcs", json={"display_name": "P"}).json()
    with app.state.session_factory() as db:
        row = db.get(EnrollmentCode, hash_secret(d["enrollment_code"]))
        row.expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
    r = TestClient(app).post("/pcs/register", json={"code": d["enrollment_code"]})
    assert r.status_code == 400
    with app.state.session_factory() as db:
        assert db.get(Pc, d["pc_id"]).api_key_hash is None


def test_heartbeat_updates_last_seen(enrolled, app):
    with app.state.session_factory() as db:
        db.get(Pc, enrolled["pc_id"]).last_seen_at = None
        db.commit()
    r = enrolled["client"].post(f"/pcs/{enrolled['pc_id']}/heartbeat",
                                headers=auth(enrolled))
    assert r.status_code == 204
    with app.state.session_factory() as db:
        assert db.get(Pc, enrolled["pc_id"]).last_seen_at is not None


def test_heartbeat_without_or_with_garbage_key_is_401(enrolled):
    url = f"/pcs/{enrolled['pc_id']}/heartbeat"
    assert enrolled["client"].post(url).status_code == 401
    assert enrolled["client"].post(
        url, headers=auth(enrolled, "dsk_garbage")).status_code == 401


def test_key_of_another_pc_is_403(enrolled, admin_client, app):
    from fastapi.testclient import TestClient
    other = admin_client.post("/admin/pcs", json={"display_name": "PC-2"}).json()
    reg = TestClient(app).post("/pcs/register",
                               json={"code": other["enrollment_code"]}).json()
    r = enrolled["client"].post(f"/pcs/{enrolled['pc_id']}/heartbeat",
                                headers=auth(enrolled, reg["api_key"]))
    assert r.status_code == 403


def test_revoked_key_is_401(enrolled, admin_client):
    assert admin_client.delete(
        f"/admin/pcs/{enrolled['pc_id']}/key").status_code == 204
    r = enrolled["client"].post(f"/pcs/{enrolled['pc_id']}/heartbeat",
                                headers=auth(enrolled))
    assert r.status_code == 401


def test_reenrolling_replaces_the_old_key(enrolled, admin_client):
    code = admin_client.post(
        f"/admin/pcs/{enrolled['pc_id']}/enrollment-code").json()["enrollment_code"]
    new_key = enrolled["client"].post(
        "/pcs/register", json={"code": code}).json()["api_key"]
    url = f"/pcs/{enrolled['pc_id']}/heartbeat"
    assert enrolled["client"].post(url, headers=auth(enrolled)).status_code == 401
    assert enrolled["client"].post(
        url, headers=auth(enrolled, new_key)).status_code == 204


def test_concurrent_code_redemption_is_atomic(admin_client, app):
    """Test that enrollment code can only be redeemed once even with concurrent requests."""
    from fastapi.testclient import TestClient
    from sqlalchemy import update

    # Create a PC
    d = admin_client.post("/admin/pcs", json={"display_name": "Concurrent-Test"}).json()
    code = d["enrollment_code"]
    pc_id = d["pc_id"]

    # Get the enrollment code row in a new session
    with app.state.session_factory() as s2:
        from central.security import hash_secret

        # Simulate a concurrent winner claiming the code first (atomic update)
        now = utcnow()
        h = hash_secret(code)
        res = s2.execute(update(EnrollmentCode)
            .where(EnrollmentCode.code_hash == h,
                   EnrollmentCode.used_at.is_(None),
                   EnrollmentCode.expires_at >= now)
            .values(used_at=now))
        s2.commit()

        # Verify the update succeeded
        assert res.rowcount == 1, "concurrent winner should claim the code"

    # Now try to use the same code - should fail with 400
    pc_client = TestClient(app)
    r = pc_client.post("/pcs/register", json={"code": code})
    assert r.status_code == 400

    # Verify PC still has no API key
    with app.state.session_factory() as db:
        pc = db.get(Pc, pc_id)
        assert pc.api_key_hash is None


def test_register_with_lone_surrogate_code_reaches_hash_secret(admin_client, app):
    """Test that JSON-escaped lone surrogates reach hash_secret (>= 8 chars)."""
    from fastapi.testclient import TestClient

    # Get response for unknown code (to compare body)
    pc_client = TestClient(app)
    unknown_r = pc_client.post("/pcs/register", json={"code": "does-not-exist-123"})
    unknown_response = unknown_r.json()
    unknown_status = unknown_r.status_code

    # Send JSON with JSON-ESCAPED lone surrogates (>= 8 chars to pass min_length)
    # This creates a string with actual lone surrogates that will reach hash_secret
    body = b'{"code":"\\ud800\\ud800\\ud800\\ud800\\ud800\\ud800\\ud800\\ud800"}'
    surrogate_r = pc_client.post(
        "/pcs/register",
        content=body,
        headers={"content-type": "application/json"}
    )

    # Should return 400 with same body as unknown code
    assert surrogate_r.status_code == unknown_status == 400
    assert surrogate_r.json() == unknown_response
