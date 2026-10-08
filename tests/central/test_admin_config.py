import copy
import json
import uuid
from pathlib import Path
import pytest

from central.config_rules import check_config


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
    r_login = client.post("/admin/login", json={
        "username": "root", "password": "correct horse battery"})
    assert r_login.status_code == 200
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
            with app.state.session_factory() as other:
                other.add(Project(id=p, name="Raced", config_json="{}", current_version=0))
                other.commit()
        return result

    monkeypatch.setattr(Session, "get", racing_get)
    r = put_project(admin_client, p, project_config)
    assert r.status_code == 200
    assert r.json()["version"] == 1
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
    put_project(admin_client, p, project_config)

    real_get = Session.get
    state = {"fired": False}

    def racing_get(self, entity, ident, *a, **kw):
        result = real_get(self, entity, ident, *a, **kw)
        if entity is Project and ident == p and not state["fired"]:
            state["fired"] = True
            with app.state.session_factory() as other:
                proj = other.get(Project, p)
                proj.current_version = 2
                proj.config_json = '{"x":1}'
                other.add(ConfigVersion(kind="project", owner_id=p, version=2,
                                       config_json='{"x":1}', created_by="other"))
                other.commit()
        return result

    monkeypatch.setattr(Session, "get", racing_get)
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
    versions = admin_client.get(f"/admin/templates/{t}/versions").json()
    assert [v["version"] for v in versions] == [3, 2, 1]


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
    assert "concurrent update, retry" in r.json()["detail"]
    assert admin_client.get(f"/admin/projects/{p}").status_code == 404


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
    assert admin_client.get(f"/admin/templates/{t}").status_code == 404


# R2: Built-in template credential guard — parametrised
_TEMPLATES = sorted((Path(__file__).resolve().parents[2] / "core" / "templates").glob("*.json"))


def test_builtin_templates_exist():
    """Verify core/templates/ has templates to test."""
    assert len(_TEMPLATES) >= 5, f"Expected >=5 templates, found {len(_TEMPLATES)}"


@pytest.mark.parametrize("path", _TEMPLATES, ids=lambda p: p.name)
def test_builtin_template_passes_credential_check(path):
    """R2: Each template config must pass credential check."""
    data = json.loads(path.read_text(encoding="utf-8"))
    config = data.get("config", data)
    check_config(config)  # raises ValueError if credentials found


# R1: Deep nesting tests with VALID JSON
def _nested_body(depth: int, template: bool) -> bytes:
    """Build valid JSON with nesting depth == depth parameter."""
    cfg = '{"a":' * depth + '1' + '}' * depth
    if template:
        return ('{"name":"T","category":"x","config":' + cfg + '}').encode()
    return ('{"name":"P","config":' + cfg + '}').encode()


def test_nested_payload_is_valid_json():
    """Sanity check: 500-level payload is valid JSON."""
    payload = _nested_body(500, False)
    parsed = json.loads(payload)  # Should not raise
    assert "config" in parsed


def test_project_deeply_nested_500_via_http_returns_422(admin_client):
    """R1: Project with 500-level nesting via HTTP -> 422."""
    payload = _nested_body(500, False)
    r = admin_client.put(
        f"/admin/projects/{pid()}",
        content=payload,
        headers={"content-type": "application/json"}
    )
    assert r.status_code == 422


def test_template_deeply_nested_500_via_http_returns_422(admin_client):
    """R1: Template with 500-level nesting via HTTP -> 422."""
    payload = _nested_body(500, True)
    r = admin_client.put(
        f"/admin/templates/{pid()}",
        content=payload,
        headers={"content-type": "application/json"}
    )
    assert r.status_code == 422


def test_project_deeply_nested_5000_via_http_returns_safe_status(admin_client):
    """R1: Project with 5000-level nesting -> 422/400 (not 500)."""
    payload = _nested_body(5000, False)
    r = admin_client.put(
        f"/admin/projects/{pid()}",
        content=payload,
        headers={"content-type": "application/json"}
    )
    # json.loads raises RecursionError on 5000 levels, which Starlette catches as 400
    # Either response (400 or 422) is safe; the key is NOT 500
    assert r.status_code in (400, 422), f"Expected 400 or 422, got {r.status_code}"


def test_template_deeply_nested_5000_via_http_returns_safe_status(admin_client):
    """R1: Template with 5000-level nesting -> 422/400 (not 500)."""
    payload = _nested_body(5000, True)
    r = admin_client.put(
        f"/admin/templates/{pid()}",
        content=payload,
        headers={"content-type": "application/json"}
    )
    assert r.status_code in (400, 422), f"Expected 400 or 422, got {r.status_code}"


# R4: Template version endpoints tests
def test_template_versions_list(admin_client):
    """GET /admin/templates/{id}/versions returns versions desc."""
    t = pid()
    admin_client.put(f"/admin/templates/{t}", json={
        "name": "T", "category": "x", "config": {"a": 1}})
    admin_client.put(f"/admin/templates/{t}", json={
        "name": "T", "category": "x", "config": {"a": 2}})

    r = admin_client.get(f"/admin/templates/{t}/versions")
    assert r.status_code == 200
    versions = r.json()
    assert [v["version"] for v in versions] == [2, 1]
    assert all("created_by" in v and "created_at" in v for v in versions)


def test_template_version_detail(admin_client):
    """GET /admin/templates/{id}/versions/{n} returns config."""
    t = pid()
    admin_client.put(f"/admin/templates/{t}", json={
        "name": "T", "category": "x", "config": {"a": 1}})

    r = admin_client.get(f"/admin/templates/{t}/versions/1")
    assert r.status_code == 200
    data = r.json()
    assert data["version"] == 1
    assert data["config"]["a"] == 1


def test_template_versions_unknown_template_404(admin_client):
    """GET /admin/templates/nope/versions -> 404."""
    r = admin_client.get("/admin/templates/nope/versions")
    assert r.status_code == 404


def test_template_version_unknown_version_404(admin_client):
    """GET /admin/templates/{id}/versions/999 -> 404."""
    t = pid()
    admin_client.put(f"/admin/templates/{t}", json={
        "name": "T", "category": "x", "config": {"a": 1}})

    r = admin_client.get(f"/admin/templates/{t}/versions/999")
    assert r.status_code == 404


def test_template_versions_unauthenticated_401(app):
    """GET /admin/templates/{id}/versions without login -> 401."""
    from starlette.testclient import TestClient
    unauthenticated = TestClient(app)

    r = unauthenticated.get(f"/admin/templates/{pid()}/versions")
    assert r.status_code == 401


def test_template_version_detail_unauthenticated_401(app):
    """GET /admin/templates/{id}/versions/{n} without login -> 401."""
    from starlette.testclient import TestClient
    unauthenticated = TestClient(app)

    r = unauthenticated.get(f"/admin/templates/{pid()}/versions/1")
    assert r.status_code == 401
