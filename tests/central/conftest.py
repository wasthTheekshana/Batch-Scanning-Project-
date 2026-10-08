"""Fixtures for the central-service tests. The whole directory is skipped
when central/requirements.txt is not installed, so the DOKScanner.exe test
run is unaffected."""
import importlib.util

import pytest

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
