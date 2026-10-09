import pytest
from sqlalchemy import inspect

from central.settings import Settings


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_all_tables_created(app):
    names = set(inspect(app.state.engine).get_table_names())
    assert {"pcs", "enrollment_codes", "admin_users", "projects",
            "project_templates", "config_versions", "pc_assignments",
            "settings", "audit_events"} <= names


def test_api_docs_are_not_exposed(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_settings_from_env_requires_strong_secret(monkeypatch):
    monkeypatch.setenv("CENTRAL_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("CENTRAL_SESSION_SECRET", "short")
    with pytest.raises(RuntimeError, match="at least 32"):
        Settings.from_env()


def test_settings_from_env_reads_values(monkeypatch):
    monkeypatch.setenv("CENTRAL_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("CENTRAL_SESSION_SECRET", "s" * 32)
    monkeypatch.setenv("CENTRAL_COOKIE_SECURE", "0")
    s = Settings.from_env()
    assert s.database_url == "sqlite://" and s.cookie_secure is False
