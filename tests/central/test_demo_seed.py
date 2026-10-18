"""The demo's sample data fills an empty database once and shows every state the dashboard can show."""
from fastapi.testclient import TestClient

from central.app import create_app
from central.crypto import DataKey, generate_key
from central.settings import Settings
from scripts.central_demo_data import ADMIN, SEARCHER, seed


def test_the_demo_data_shows_every_state_and_is_added_only_once(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'd.db'}", session_secret="x" * 40,
                              cookie_secure=False, data_key=DataKey(generate_key())))
    assert seed(app) is True and seed(app) is False
    c = TestClient(app)
    c.headers["X-CSRF-Token"] = c.post("/admin/login", json={"username": ADMIN[0], "password": ADMIN[1]}).json()["csrf_token"]
    summary = c.get("/admin/fleet").json()["summary"]
    assert (summary["online"], summary["offline"], summary["not_registered"], summary["behind"]) == (2, 1, 1, 1)
    assert {a["kind"] for a in c.get("/admin/alerts").json()["alerts"]} == {"behind", "offline", "errors"}
    assert c.get("/admin/documents").json()["total"] > 30
    s = TestClient(app)
    s.headers["X-CSRF-Token"] = s.post("/admin/login", json={"username": SEARCHER[0], "password": SEARCHER[1]}).json()["csrf_token"]
    assert s.get("/admin/documents").json()["total"] > 30
