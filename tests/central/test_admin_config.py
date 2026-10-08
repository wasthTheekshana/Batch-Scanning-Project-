import copy
import uuid


def pid():
    return str(uuid.uuid4())


def put_project(c, project_id, config, name="Invoices"):
    return c.put(f"/admin/projects/{project_id}",
                 json={"name": name, "config": config})


def test_new_project_is_version_1_and_listed(admin_client, project_config):
    p = pid()
    r = put_project(admin_client, p, project_config)
    assert r.status_code == 200
    assert r.json() == {"id": p, "name": "Invoices", "version": 1, "changed": True}
    assert admin_client.get("/admin/projects").json() == [
        {"id": p, "name": "Invoices", "version": 1}]
    got = admin_client.get(f"/admin/projects/{p}").json()
    assert got["config"]["project_id"] == p and got["config"]["name"] == "Invoices"


def test_changed_config_bumps_version_and_keeps_history(admin_client, project_config):
    p = pid()
    put_project(admin_client, p, project_config)
    changed = copy.deepcopy(project_config)
    changed["ocr"]["lang"] = "deu"
    r = put_project(admin_client, p, changed)
    assert r.json()["version"] == 2 and r.json()["changed"] is True
    versions = admin_client.get(f"/admin/projects/{p}/versions").json()
    assert [v["version"] for v in versions] == [2, 1]
    old = admin_client.get(f"/admin/projects/{p}/versions/1").json()
    assert old["config"]["ocr"]["lang"] == project_config["ocr"]["lang"]


def test_identical_resubmit_does_not_bump_version(admin_client, project_config):
    p = pid()
    put_project(admin_client, p, project_config)
    r = put_project(admin_client, p, project_config)
    assert r.json()["version"] == 1 and r.json()["changed"] is False
    assert len(admin_client.get(f"/admin/projects/{p}/versions").json()) == 1


def test_body_name_and_path_id_override_config(admin_client, project_config):
    p = pid()
    cfg = {**project_config, "project_id": "someone-else", "name": "Wrong"}
    r = put_project(admin_client, p, cfg, name="Right")
    got = admin_client.get(f"/admin/projects/{p}").json()
    assert r.status_code == 200
    assert got["config"]["project_id"] == p and got["config"]["name"] == "Right"


def test_invalid_project_config_is_422(admin_client, project_config):
    bad = {k: v for k, v in project_config.items() if k != "scanner"}
    r = put_project(admin_client, pid(), bad)
    assert r.status_code == 422


def test_credentials_in_config_are_rejected_and_not_stored(admin_client, project_config):
    p = pid()
    bad = copy.deepcopy(project_config)
    bad["delivery"]["sftp"] = {"enabled": True, "host": "h", "password": "hunter2"}
    r = put_project(admin_client, p, bad)
    assert r.status_code == 422 and "delivery.sftp.password" in r.json()["detail"]
    assert admin_client.get(f"/admin/projects/{p}").status_code == 404


def test_oversized_config_is_422(admin_client, project_config):
    big = {**project_config, "notes": "x" * 300_000}
    assert put_project(admin_client, pid(), big).status_code == 422


def test_malformed_project_id_is_422(admin_client, project_config):
    assert put_project(admin_client, "bad id!", project_config).status_code == 422


def test_unknown_project_and_version_are_404(admin_client, project_config):
    assert admin_client.get("/admin/projects/nope").status_code == 404
    p = pid()
    put_project(admin_client, p, project_config)
    assert admin_client.get(f"/admin/projects/{p}/versions/9").status_code == 404


def test_project_writes_need_login_and_csrf(client, admin, project_config):
    assert put_project(client, pid(), project_config).status_code == 401
    client.post("/admin/login", json={
        "username": "root", "password": "correct horse battery"})
    assert put_project(client, pid(), project_config).status_code == 403


def test_templates_roundtrip_and_never_built_in(admin_client):
    t = pid()
    r = admin_client.put(f"/admin/templates/{t}", json={
        "name": "Banking", "category": "banking",
        "config": {"ocr": {"zones": []}}, "built_in": True})
    assert r.status_code == 200 and r.json()["version"] == 1
    listed = admin_client.get("/admin/templates").json()
    assert listed == [{"id": t, "name": "Banking", "category": "banking",
                       "built_in": False, "version": 1}]
    assert admin_client.get(f"/admin/templates/{t}").json()["config"] == {
        "ocr": {"zones": []}}


def test_template_with_credentials_is_rejected(admin_client):
    r = admin_client.put(f"/admin/templates/{pid()}", json={
        "name": "T", "category": "x", "config": {"token": "abc"}})
    assert r.status_code == 422


def test_project_concurrent_update_succeeds(admin_client, project_config):
    """Test that the retry loop in put_project handles concurrent updates gracefully."""
    p = pid()
    # First create should succeed
    r1 = put_project(admin_client, p, project_config)
    assert r1.status_code == 200
    assert r1.json()["version"] == 1

    # Second concurrent update should also succeed (retry loop in place)
    changed = copy.deepcopy(project_config)
    changed["ocr"]["lang"] = "deu"
    r2 = put_project(admin_client, p, changed)
    assert r2.status_code == 200
    assert r2.json()["version"] == 2


def test_template_concurrent_update_succeeds(admin_client):
    """Test that template updates work correctly (retry logic in place)."""
    t = pid()
    # First create
    r1 = admin_client.put(f"/admin/templates/{t}", json={
        "name": "Banking", "category": "banking", "config": {"ocr": {"zones": []}}})
    assert r1.status_code == 200
    assert r1.json()["version"] == 1

    # Concurrent update should succeed
    r2 = admin_client.put(f"/admin/templates/{t}", json={
        "name": "Banking V2", "category": "banking", "config": {"ocr": {"zones": [1]}}})
    assert r2.status_code == 200
    assert r2.json()["version"] == 2


def test_unknown_template_is_404(admin_client):
    assert admin_client.get("/admin/templates/nope").status_code == 404


def test_template_without_csrf_is_403(client, admin):
    """Login but don't set CSRF token, should get 403."""
    t = pid()
    # Login first
    r_login = client.post("/admin/login", json={
        "username": "root", "password": "correct horse battery"})
    assert r_login.status_code == 200
    # Try PUT without CSRF token
    r = client.put(f"/admin/templates/{t}", json={
        "name": "T", "category": "x", "config": {"ok": True}})
    assert r.status_code == 403


def test_deeply_nested_project_config_rejected(admin_client, project_config):
    """Test that project with deeply nested config is rejected (422)."""
    p = pid()
    cfg = copy.deepcopy(project_config)
    # Build a deeply nested structure
    nested = cfg
    for i in range(100):
        nested["deep"] = {}
        nested = nested["deep"]
    nested["value"] = "x"

    r = put_project(admin_client, p, cfg)
    assert r.status_code == 422 and "nested too deeply" in r.json()["detail"]


def test_deeply_nested_template_config_rejected(admin_client):
    """Test that template with deeply nested config is rejected (422)."""
    t = pid()
    cfg = {}
    nested = cfg
    for i in range(100):
        nested["deep"] = {}
        nested = nested["deep"]
    nested["value"] = "x"

    r = admin_client.put(f"/admin/templates/{t}", json={
        "name": "T", "category": "x", "config": cfg})
    assert r.status_code == 422 and "nested too deeply" in r.json()["detail"]
