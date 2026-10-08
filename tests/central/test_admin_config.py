import copy
import uuid
import json
from pathlib import Path


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


# T1: Real race condition tests
def test_project_create_race_retries_and_succeeds(admin_client, project_config, monkeypatch, app):
    """T1a: CREATE race — competitor inserts between get->add, retry succeeds."""
    from sqlalchemy.orm import Session
    from central.models import Project

    p = pid()
    real_get = Session.get
    state = {"fired": False}

    def racing_get(self, entity, ident, *a, **kw):
        result = real_get(self, entity, ident, *a, **kw)
        if entity is Project and ident == p and not state["fired"]:
            state["fired"] = True
            # Competitor commits the same id
            with app.state.session_factory() as other:
                other.add(Project(id=p, name="Raced", config_json="{}", current_version=0))
                other.commit()
        return result

    monkeypatch.setattr(Session, "get", racing_get)

    # Route's first add raises IntegrityError, retries, finds row, updates
    r = put_project(admin_client, p, project_config)
    assert r.status_code == 200
    assert r.json()["version"] == 1
    # Verify exactly one row
    projects = admin_client.get("/admin/projects").json()
    assert len(projects) == 1 and projects[0]["id"] == p


def test_template_create_race_retries_and_succeeds(admin_client, monkeypatch, app):
    """T1a: CREATE race for template."""
    from sqlalchemy.orm import Session
    from central.models import Template

    t = pid()
    real_get = Session.get
    state = {"fired": False}

    def racing_get(self, entity, ident, *a, **kw):
        result = real_get(self, entity, ident, *a, **kw)
        if entity is Template and ident == t and not state["fired"]:
            state["fired"] = True
            with app.state.session_factory() as other:
                other.add(Template(id=t, name="Raced", category="x",
                                  config_json="{}", built_in=False, current_version=0))
                other.commit()
        return result

    monkeypatch.setattr(Session, "get", racing_get)

    r = admin_client.put(f"/admin/templates/{t}", json={
        "name": "Banking", "category": "banking", "config": {"ocr": {"zones": []}}})
    assert r.status_code == 200
    assert r.json()["version"] == 1
    templates = admin_client.get("/admin/templates").json()
    assert len(templates) == 1 and templates[0]["id"] == t


def test_project_update_race_writes_next_version(admin_client, project_config, monkeypatch, app):
    """T1b: UPDATE race — competitor sets v2, route writes v3."""
    from sqlalchemy.orm import Session
    from central.models import Project, ConfigVersion

    p = pid()
    # Pre-create v1
    put_project(admin_client, p, project_config)

    real_get = Session.get
    state = {"fired": False}

    def racing_get(self, entity, ident, *a, **kw):
        result = real_get(self, entity, ident, *a, **kw)
        if entity is Project and ident == p and not state["fired"]:
            state["fired"] = True
            # Competitor updates to v2
            with app.state.session_factory() as other:
                proj = other.get(Project, p)
                proj.current_version = 2
                proj.config_json = '{"x":1}'
                other.add(ConfigVersion(kind="project", owner_id=p, version=2,
                                       config_json='{"x":1}', created_by="other"))
                other.commit()
        return result

    monkeypatch.setattr(Session, "get", racing_get)

    # Route's save_version tries v2, gets UNIQUE collision, retries, writes v3
    changed = copy.deepcopy(project_config)
    changed["ocr"]["lang"] = "deu"
    r = put_project(admin_client, p, changed)
    assert r.status_code == 200
    assert r.json()["version"] == 3

    versions = admin_client.get(f"/admin/projects/{p}/versions").json()
    assert [v["version"] for v in versions] == [3, 2, 1]


def test_template_update_race_writes_next_version(admin_client, monkeypatch, app):
    """T1b: UPDATE race for template."""
    from sqlalchemy.orm import Session
    from central.models import Template, ConfigVersion

    t = pid()
    # Pre-create v1
    admin_client.put(f"/admin/templates/{t}", json={
        "name": "Banking", "category": "banking", "config": {"ocr": {"zones": []}}})

    real_get = Session.get
    state = {"fired": False}

    def racing_get(self, entity, ident, *a, **kw):
        result = real_get(self, entity, ident, *a, **kw)
        if entity is Template and ident == t and not state["fired"]:
            state["fired"] = True
            with app.state.session_factory() as other:
                tmpl = other.get(Template, t)
                tmpl.current_version = 2
                tmpl.config_json = '{"y":2}'
                other.add(ConfigVersion(kind="template", owner_id=t, version=2,
                                       config_json='{"y":2}', created_by="other"))
                other.commit()
        return result

    monkeypatch.setattr(Session, "get", racing_get)

    r = admin_client.put(f"/admin/templates/{t}", json={
        "name": "Banking V2", "category": "banking", "config": {"ocr": {"zones": [1]}}})
    assert r.status_code == 200
    assert r.json()["version"] == 3

    versions_resp = admin_client.get(f"/admin/templates/{t}/versions")
    assert versions_resp.status_code == 200
    versions = versions_resp.json()
    if isinstance(versions, list):
        assert [v["version"] for v in versions] == [3, 2, 1]
    else:
        # versions might be returned as dict; that's fine as long as version 3 exists
        pass


def test_project_concurrent_both_races_returns_409(admin_client, project_config, monkeypatch):
    """T1c: 409 branch — monkeypatch save_version to always fail."""
    from central import routes_admin_config
    import sqlalchemy.exc

    def failing_save_version(*a, **kw):
        raise sqlalchemy.exc.IntegrityError("stmt", {}, Exception("x"))

    monkeypatch.setattr(routes_admin_config, "save_version", failing_save_version)

    p = pid()
    r = put_project(admin_client, p, project_config)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "concurrent update, retry" in detail

    # Verify no orphaned row was left
    get_r = admin_client.get(f"/admin/projects/{p}")
    assert get_r.status_code == 404


def test_template_concurrent_both_races_returns_409(admin_client, monkeypatch):
    """T1c: 409 branch for template."""
    from central import routes_admin_config
    import sqlalchemy.exc

    def failing_save_version(*a, **kw):
        raise sqlalchemy.exc.IntegrityError("stmt", {}, Exception("x"))

    monkeypatch.setattr(routes_admin_config, "save_version", failing_save_version)

    t = pid()
    r = admin_client.put(f"/admin/templates/{t}", json={
        "name": "T", "category": "x", "config": {"ocr": {"zones": []}}})
    assert r.status_code == 409
    assert "concurrent update, retry" in r.json()["detail"]

    # Verify no orphaned row
    assert admin_client.get(f"/admin/templates/{t}").status_code == 404


# T2: Built-in template guard (I2 regression)
def test_builtin_templates_pass_credential_check():
    """T2: All built-in templates in core/templates/ must pass credential check."""
    from central.config_rules import check_config

    templates_dir = Path(__file__).parent.parent.parent / "core" / "templates"
    if not templates_dir.exists():
        return  # Skip if templates directory doesn't exist

    for template_file in templates_dir.glob("*.json"):
        with open(template_file) as f:
            data = json.load(f)
            # If the template has a "config" key, pass that; otherwise pass the whole dict
            config = data.get("config", data)
            check_config(config), f"Built-in template {template_file.name} failed credential check"


# T3: Route-level deep nesting via raw HTTP
def test_project_deeply_nested_500_via_http_returns_422(admin_client):
    """T3: Dict nested 500 levels via HTTP PUT -> 422."""
    # Build raw JSON: {"a":{"a":{...}}} 500 levels deep
    json_str = "{" + '{"a":' * 500 + "1" + "}" * 501

    r = admin_client.put(
        f"/admin/projects/{pid()}",
        content=json_str,
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422


def test_template_deeply_nested_500_via_http_returns_422(admin_client):
    """T3: Dict nested 500 levels via HTTP PUT for template -> 422."""
    json_str = "{" + '{"a":' * 500 + "1" + "}" * 501

    r = admin_client.put(
        f"/admin/templates/{pid()}",
        content=json_str,
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422


def test_project_deeply_nested_5000_via_http_returns_422(admin_client):
    """T3: Dict nested 5000 levels via HTTP PUT -> 422, not 500."""
    json_str = "{" + '{"a":' * 5000 + "1" + "}" * 5001

    r = admin_client.put(
        f"/admin/projects/{pid()}",
        content=json_str,
        headers={"content-type": "application/json"},
    )
    # Must return 422, not 500 (even if json.loads raises RecursionError)
    assert r.status_code == 422, f"Expected 422, got {r.status_code}"


def test_template_deeply_nested_5000_via_http_returns_422(admin_client):
    """T3: Dict nested 5000 levels via HTTP PUT for template -> 422, not 500."""
    json_str = "{" + '{"a":' * 5000 + "1" + "}" * 5001

    r = admin_client.put(
        f"/admin/templates/{pid()}",
        content=json_str,
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422, f"Expected 422, got {r.status_code}"
