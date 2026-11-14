"""Fixtures for the central-service tests. The whole directory is skipped
when central/requirements.txt is not installed, so the DOKScanner.exe test
run is unaffected."""
import importlib.util

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "real_bcrypt: run with the production bcrypt cost factor")


_DEPS = ("fastapi", "sqlalchemy", "httpx", "itsdangerous")
if not all(importlib.util.find_spec(m) for m in _DEPS):
    collect_ignore_glob = ["test_*.py"]
else:
    from fastapi.testclient import TestClient

    from central.app import create_app
    from central.settings import Settings

    @pytest.fixture()
    def settings(tmp_path):
        return Settings(
            database_url=f"sqlite:///{tmp_path / 'central.db'}",
            session_secret="x" * 40, cookie_secure=False)

    @pytest.fixture()
    def app(settings):
        return create_app(settings)

    @pytest.fixture()
    def client(app):
        with TestClient(app) as c:
            yield c

    import bcrypt

    import central.security

    _CHEAP_ROUNDS = 4
    _CHEAP_GENSALT = bcrypt.gensalt  # captured before any patching
    _CHEAP_DUMMY = bcrypt.hashpw(
        central.security._prehash("timing-equaliser"),
        _CHEAP_GENSALT(rounds=_CHEAP_ROUNDS)).decode()

    @pytest.fixture(autouse=True)
    def _cheap_bcrypt(request, monkeypatch):
        """bcrypt at production cost (12) dominates the suite's runtime.
        Opt out with @pytest.mark.real_bcrypt."""
        if request.node.get_closest_marker("real_bcrypt"):
            return
        monkeypatch.setattr(
            central.security.bcrypt, "gensalt",
            lambda rounds=_CHEAP_ROUNDS, prefix=b"2b": _CHEAP_GENSALT(
                rounds=_CHEAP_ROUNDS, prefix=prefix))
        monkeypatch.setattr(central.security, "_DUMMY_HASH", _CHEAP_DUMMY)

    from central.manage import create_admin

    @pytest.fixture()
    def admin(app):
        create_admin(app.state.session_factory, "root", "correct horse battery")

    @pytest.fixture()
    def admin_client(client, admin):
        r = client.post("/admin/login", json={
            "username": "root", "password": "correct horse battery"})
        assert r.status_code == 200
        client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        return client

    @pytest.fixture()
    def enrolled(admin_client, app):
        d = admin_client.post("/admin/pcs",
                              json={"display_name": "PC-1"}).json()
        pc_client = TestClient(app)  # not entered: no lifespan, no admin cookie
        reg = pc_client.post("/pcs/register",
                             json={"code": d["enrollment_code"]})
        assert reg.status_code == 200
        return {"pc_id": d["pc_id"], "api_key": reg.json()["api_key"],
                "client": pc_client, "code": d["enrollment_code"]}

    @pytest.fixture()
    def project_config(tmp_path):
        from projects.profile_manager import ProfileManager
        return ProfileManager(tmp_path / "pm").create("Seed")
