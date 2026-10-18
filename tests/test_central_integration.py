"""End-to-end: the real DOKScanner sync client against the real central app.

The central FastAPI app runs in-process (TestClient); CentralClient talks to
it through an injected transport, so the real request/response contract is
exercised without a socket. Skipped when the central dependencies are absent.
"""
import copy
import itertools

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("httpx")
pytest.importorskip("itsdangerous")

import bcrypt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import central.security  # noqa: E402
from central.app import create_app  # noqa: E402
from central.manage import create_admin  # noqa: E402
from central.settings import Settings  # noqa: E402
from core.central_client import CentralClient, CentralUnavailable  # noqa: E402
from core.central_state import CentralState  # noqa: E402
from core.central_sync import CentralSyncService  # noqa: E402
from core.profile_manager import ProfileManager  # noqa: E402
from data.db import Database  # noqa: E402

BASE = "https://central.test"
_REAL_GENSALT = bcrypt.gensalt
_PID_COUNTER = itertools.count(1)


class Stack:
    """One central app + one PC, wired together."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.calls = 0                  # transport calls made by the PC
        self.down = False               # simulate a dead server
        settings = Settings(
            database_url=f"sqlite:///{tmp_path / 'central.db'}",
            session_secret="s" * 40, cookie_secure=False)
        self.app = create_app(settings)
        create_admin(self.app.state.session_factory, "root",
                     "correct horse battery")
        self.admin = TestClient(self.app)
        token = self.admin.post("/admin/login", json={
            "username": "root", "password": "correct horse battery"
        }).json()["csrf_token"]
        self.admin.headers["X-CSRF-Token"] = token
        self.pc_http = TestClient(self.app)        # separate cookie jar: the PC

        self.root = tmp_path / "pc"
        self.root.mkdir()
        self.db = Database(self.root / "pc.db")
        self.pm = ProfileManager(self.root / "projects", self.db)
        self.state = CentralState(self.root)
        self.svc = CentralSyncService(
            self.db, self.pm, self.state, self.root,
            client_factory=lambda url, t: CentralClient(
                url, transport=self._transport, timeout=t))

    def _transport(self, method, url, headers, body, timeout):
        self.calls += 1
        if self.down:
            raise CentralUnavailable("server down (simulated)")
        assert url.startswith(BASE)
        r = self.pc_http.request(method, url[len(BASE):], headers=headers,
                                 content=body)
        return r.status_code, r.content

    def close(self):
        self.db.close()
        self.app.state.engine.dispose()

    # -- admin helpers -----------------------------------------------------------
    def enrol(self, name="Counter-1"):
        pc = self.admin.post("/admin/pcs", json={"display_name": name}).json()
        self.svc.register(BASE, pc["enrollment_code"])
        return pc

    def seed_config(self):
        return ProfileManager(self.tmp / "central_pm").create("Seed")

    def publish(self, name, config, push_to=()):
        pid = f"aaaaaaaa-0000-0000-0000-{next(_PID_COUNTER):012d}"
        r = self.admin.put(f"/admin/projects/{pid}",
                           json={"name": name, "config": config})
        assert r.status_code == 200, r.text
        if push_to:
            r = self.admin.post(f"/admin/projects/{pid}/push",
                                json={"pc_ids": list(push_to)})
            assert r.status_code == 200, r.text
        return pid


@pytest.fixture()
def s(tmp_path, monkeypatch):
    # cheap bcrypt, same trick as tests/central/conftest.py
    monkeypatch.setattr(
        central.security.bcrypt, "gensalt",
        lambda rounds=4, prefix=b"2b": _REAL_GENSALT(rounds=4, prefix=prefix))
    stack = Stack(tmp_path)
    yield stack
    stack.close()


def test_full_round_trip(s):
    pc = s.enrol()
    assert s.state.is_registered()

    cfg = s.seed_config()
    cfg["ocr"]["lang"] = "deu"
    pid = s.publish("Invoices", cfg, push_to=[pc["pc_id"]])

    res = s.svc.sync_once()
    assert res.status == "ok" and res.applied == [pid]
    local = s.pm.load(pid)
    assert local["ocr"]["lang"] == "deu" and local["name"] == "Invoices"
    assert local["export"]["output_path"].endswith("output")

    # the PC customises its own delivery path; central edits the project
    local["delivery"]["local_folder"]["path"] = "D:/pc-specific/out"
    s.pm.save(local)
    cfg2 = copy.deepcopy(cfg)
    cfg2["ocr"]["lang"] = "fra"
    assert s.admin.put(f"/admin/projects/{pid}",
                       json={"name": "Invoices", "config": cfg2}
                       ).status_code == 200
    res = s.svc.sync_once()
    assert res.applied == [pid]
    after = s.pm.load(pid)
    assert after["ocr"]["lang"] == "fra"
    assert after["delivery"]["local_folder"]["path"] == "D:/pc-specific/out"

    # nothing changed -> nothing applied
    res = s.svc.sync_once()
    assert res.unchanged == 1 and res.applied == []

    # admin unassigns -> PC keeps the project but stops managing it
    assert s.admin.delete(
        f"/admin/projects/{pid}/assignments/{pc['pc_id']}").status_code == 204
    res = s.svc.sync_once()
    assert res.unassigned == [pid]
    assert s.db.get_central_project(pid)["managed"] == 0
    assert s.pm.load(pid)["ocr"]["lang"] == "fra"


def test_malicious_rule_folder_is_skipped_and_the_other_project_applies(s):
    pc = s.enrol()
    evil = s.seed_config()
    evil["rules"] = {"rule_set_id": "r", "version": 1, "rules": [{
        "id": "r1", "name": "escape",
        "condition": {"field": "doc_type", "op": "eq", "value": "x"},
        "actions": [{"type": "set_folder", "value": "../../escape"}]}]}
    good = s.seed_config()
    good["ocr"]["lang"] = "deu"
    evil_id = s.publish("Evil", evil, push_to=[pc["pc_id"]])
    good_id = s.publish("Good", good, push_to=[pc["pc_id"]])

    res = s.svc.sync_once()

    assert res.status == "ok"
    assert list(res.errors) == [evil_id]
    assert "set_folder" in res.errors[evil_id]
    assert res.applied == [good_id]
    assert s.db.get_project(evil_id) is None
    assert s.db.get_central_project(evil_id) is None
    assert s.pm.load(good_id)["ocr"]["lang"] == "deu"
    # pins "skip + sibling applies": the hostile project left no trace on disk
    # (the export-time guard itself is covered in test_export.py)
    assert not (s.root / "escape").exists()
    assert not (s.tmp / "escape").exists()
    assert not any("escape" in p.name for p in s.root.rglob("*"))
    assert {p.name for p in (s.root / "projects").glob("*.json")} == {
        f"{good_id}.json"}


def test_stale_pc_sees_removed_assignment_as_unassigned(s):
    pc = s.enrol()
    pid = s.publish("Invoices", s.seed_config(), push_to=[pc["pc_id"]])
    assert s.svc.sync_once().applied == [pid]

    # the assignment is removed while the PC is offline
    s.down = True
    assert s.svc.sync_once().status == "unavailable"
    assert s.admin.delete(
        f"/admin/projects/{pid}/assignments/{pc['pc_id']}").status_code == 204
    s.down = False

    res = s.svc.sync_once()
    assert res.status == "ok" and res.unassigned == [pid]
    assert s.db.get_central_project(pid)["managed"] == 0
    assert s.pm.load(pid)["name"] == "Invoices"


def test_server_down_keeps_local_state_and_recovers(s):
    pc = s.enrol()
    cfg = s.seed_config()
    pid = s.publish("Invoices", cfg, push_to=[pc["pc_id"]])
    assert s.svc.sync_once().applied == [pid]
    version = s.db.get_central_project(pid)["central_version"]
    local_before = s.pm.load(pid)

    s.down = True
    res = s.svc.sync_once()
    assert res.status == "unavailable"
    assert s.state.settings.auth_failed is False
    assert s.state.is_registered()
    assert s.pm.load(pid) == local_before
    assert s.db.get_central_project(pid)["central_version"] == version
    assert s.db.get_central_project(pid)["managed"] == 1

    # while it was down, central changed the project; recovery picks it up
    cfg2 = copy.deepcopy(cfg)
    cfg2["ocr"]["lang"] = "fra"
    assert s.admin.put(f"/admin/projects/{pid}",
                       json={"name": "Invoices", "config": cfg2}
                       ).status_code == 200
    s.down = False
    res = s.svc.sync_once()
    assert res.status == "ok" and res.applied == [pid]
    assert s.pm.load(pid)["ocr"]["lang"] == "fra"


def test_revoked_key_stops_polling_until_re_enrolment(s):
    pc = s.enrol()
    pid = s.publish("Invoices", s.seed_config(), push_to=[pc["pc_id"]])
    assert s.svc.sync_once().applied == [pid]

    assert s.admin.delete(f"/admin/pcs/{pc['pc_id']}/key").status_code == 204
    assert s.svc.sync_once().status == "auth_failed"
    assert s.state.settings.auth_failed is True

    calls = s.calls
    for _ in range(3):
        assert s.svc.sync_once().status == "auth_failed"
    assert s.calls == calls                       # no hammering

    code = s.admin.post(
        f"/admin/pcs/{pc['pc_id']}/enrollment-code").json()["enrollment_code"]
    s.svc.register(BASE, code)
    assert s.state.settings.auth_failed is False
    res = s.svc.sync_once()
    assert res.status == "ok" and res.unchanged == 1
    assert s.calls > calls


def test_admin_cannot_publish_a_config_with_a_password(s):
    cfg = s.seed_config()
    cfg["delivery"]["sftp"] = {"enabled": True, "host": "h", "password": "pw"}
    pid = "bbbbbbbb-1111-2222-3333-444444444444"
    r = s.admin.put(f"/admin/projects/{pid}",
                    json={"name": "Leaky", "config": cfg})
    assert r.status_code == 422

    # and so no PC can ever receive it: nothing is stored or assigned
    pc = s.enrol()
    assert s.admin.post(f"/admin/projects/{pid}/push",
                        json={"pc_ids": [pc["pc_id"]]}).status_code == 404
    res = s.svc.sync_once()
    assert res.status == "ok" and res.applied == [] and res.errors == {}


# -- the fleet view: what the PC reports reaches the admin's screens ----------------------------------------
def test_the_pc_report_reaches_the_admin_fleet_view(s):
    from version import __version__
    pc = s.enrol()
    pid = s.publish("Bank files", s.seed_config(), push_to=[pc["pc_id"]])
    fleet = s.admin.get("/admin/fleet").json()
    assert fleet["pcs"][0]["rollout"] == "unknown"              # assigned, the PC has not reported yet
    assert s.svc.sync_once().status == "ok"                      # the PC pulls the project and reports
    pc_row = s.admin.get("/admin/fleet").json()["pcs"][0]
    assert pc_row["status"] == "online" and pc_row["app_version"] == __version__
    assert pc_row["rollout"] == "current"
    assert [(p["project_id"], p["state"]) for p in pc_row["projects"]] == [(pid, "current")]
    assert pc_row["today"] == {"documents": 0, "pages": 0, "batches": 0, "errors": 0}


def test_a_new_version_published_shows_the_pc_as_behind_until_it_syncs(s):
    pc = s.enrol()
    config = s.seed_config()
    pid = s.publish("Bank files", config, push_to=[pc["pc_id"]])
    s.svc.sync_once()
    assert s.admin.get("/admin/fleet").json()["pcs"][0]["rollout"] == "current"
    config["ocr"]["lang"] = "eng+sin"
    assert s.admin.put(f"/admin/projects/{pid}", json={"name": "Bank files", "config": config}).status_code == 200
    assert s.admin.get("/admin/fleet").json()["pcs"][0]["rollout"] == "behind"
    s.svc.sync_once()
    assert s.admin.get("/admin/fleet").json()["pcs"][0]["rollout"] == "current"
