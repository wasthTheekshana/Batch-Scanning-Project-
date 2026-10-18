"""A real PC database and sender against a real central app: documents scanned while the server is down arrive,
exactly once, after it returns."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from central.app import create_app
from central.crypto import DataKey, generate_key
from central.manage import create_admin
from central.models import PcAssignment, Project
from central.settings import Settings
from core.central_client import CentralClient, CentralUnavailable
from core.central_documents import DocumentPusher
from data.db import Database

NIC = "553216789V"


@pytest.fixture()
def rig(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'c.db'}", session_secret="x" * 40,
                              cookie_secure=False, data_key=DataKey(generate_key())))
    create_admin(app.state.session_factory, "root", "correct horse battery")
    admin = TestClient(app)
    token = admin.post("/admin/login", json={"username": "root", "password": "correct horse battery"}).json()
    admin.headers["X-CSRF-Token"] = token["csrf_token"]
    made = admin.post("/admin/pcs", json={"display_name": "Galle desk"}).json()
    api_key = TestClient(app).post("/pcs/register", json={"code": made["enrollment_code"]}).json()["api_key"]
    with app.state.session_factory() as s:
        s.add(Project(id="p1", name="Bank", config_json="{}", current_version=1))
        s.commit()
        s.add(PcAssignment(pc_id=made["pc_id"], project_id="p1"))
        s.commit()

    http = TestClient(app)
    outage = SimpleNamespace(on=False)

    def transport(method, url, headers, body, timeout):
        if outage.on:
            raise CentralUnavailable("connection refused")
        r = http.request(method, url.removeprefix("https://central.test"), headers=headers, content=body)
        return r.status_code, r.content

    db = Database(tmp_path / "pc.db")
    db.insert_project("p1", "Bank", {})
    db.insert_batch("b1", "p1", "kamal")
    state = SimpleNamespace(settings=SimpleNamespace(server_url="https://central.test", pc_id=made["pc_id"],
                                                     auth_failed=False),
                            is_registered=lambda: True, api_key=lambda: api_key)
    db.outbox_gate = state.is_registered
    clock = SimpleNamespace(now=datetime.now(timezone.utc))
    pusher = DocumentPusher(db, state, client_factory=lambda url, t: CentralClient(url, transport=transport),
                            now=lambda: clock.now)
    yield SimpleNamespace(app=app, admin=admin, db=db, pusher=pusher, outage=outage, clock=clock, tmp=tmp_path)
    db.close()


def scan(rig, doc_id, name="Kamal Perera"):
    pdf = rig.tmp / f"{doc_id}.pdf"
    pdf.write_bytes(b"%PDF-1.4 " + doc_id.encode())
    rig.db.insert_document(doc_id, "b1", "p1", page_range="1-2", storage_path=str(pdf), validation_status="approved",
                           extracted_fields={"nic": NIC, "full_name": name, "branch": "Galle"})


def found(rig, q=NIC):
    return rig.admin.get("/admin/documents", params={"q": q}).json()


def test_documents_scanned_during_an_outage_arrive_exactly_once_when_the_server_returns(rig):
    rig.outage.on = True
    scan(rig, "d1")
    scan(rig, "d2", name="Nimal Silva")
    assert rig.pusher.push_once().stopped == "unavailable"
    assert rig.db.outbox_stats()["waiting"] == 2 and found(rig)["total"] == 0

    rig.outage.on = False
    rig.clock.now += timedelta(hours=2)
    assert rig.pusher.push_once().sent == 2
    rig.pusher.push_once()
    rig.pusher.push_once()                                          # nothing left to say, however often it runs

    body = found(rig)
    assert body["total"] == 2
    assert sorted(i["fields"]["full_name"] for i in body["items"]) == ["Kamal Perera", "Nimal Silva"]
    assert all(i["revision"] == 1 for i in body["items"])
    assert rig.db.outbox_stats()["waiting"] == 0


def test_a_correction_made_on_the_pc_becomes_the_next_revision_centrally(rig):
    scan(rig, "d1")
    rig.pusher.push_once()
    rig.db.update_document("d1", extracted_fields={"nic": NIC, "full_name": "Kamal A Perera", "branch": "Galle"})
    rig.pusher.push_once()
    doc = found(rig)["items"][0]
    detail = rig.admin.get(f"/admin/documents/{doc['id']}").json()
    assert detail["revision"] == 2 and detail["fields"]["full_name"] == "Kamal A Perera"
    assert detail["files"][0]["file_name"] == "d1.pdf" and detail["files"][0]["file_state"] == "metadata_only"


def test_a_lost_reply_does_not_duplicate_anything(rig):
    scan(rig, "d1")
    original = rig.pusher._factory

    class LosesReply:
        def __init__(self, inner):
            self.inner = inner

        def put_document(self, *a):
            self.inner.put_document(*a)                              # the server stored it ...
            if a[3] == "index":
                raise CentralUnavailable("reply lost")               # ... but the PC never heard

    rig.pusher._factory = lambda url, t: LosesReply(original(url, t))
    rig.pusher.push_once()
    rig.pusher._factory = original
    rig.clock.now += timedelta(hours=2)
    assert rig.pusher.push_once().sent == 1
    assert found(rig)["total"] == 1
    assert found(rig)["items"][0]["revision"] == 1
