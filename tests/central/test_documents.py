"""Index sync, server side: a PC reports a document's details and index fields (idempotently), they are encrypted
at rest, an administrator finds them by an exact identifier, and a searcher sees only their projects."""
import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from central.app import create_app
from central.crypto import generate_key
from central.manage import create_admin
from central.models import PcAssignment, Project
from central.settings import Settings

NIC = "553216789V"
FIELDS = {"nic": NIC, "full_name": "Kamal Perera", "account_no": "1234567890", "branch": "Galle"}
SHA = hashlib.sha256(b"pdf one").hexdigest()


@pytest.fixture()
def keyed_app(tmp_path):
    return create_app(Settings(database_url=f"sqlite:///{tmp_path / 'c.db'}", session_secret="x" * 40,
                               cookie_secure=False, data_key=__import__("central.crypto", fromlist=["DataKey"]).DataKey(generate_key())))


def login(app, name="root", password="correct horse battery"):
    c = TestClient(app)
    r = c.post("/admin/login", json={"username": name, "password": password})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


@pytest.fixture()
def world(keyed_app):
    """An admin, one enrolled PC, and helpers to send documents as that PC."""
    create_admin(keyed_app.state.session_factory, "root", "correct horse battery")
    admin = login(keyed_app)
    made = admin.post("/admin/pcs", json={"display_name": "Galle desk"}).json()
    pc = TestClient(keyed_app)
    key = pc.post("/pcs/register", json={"code": made["enrollment_code"]}).json()["api_key"]
    pc.headers["Authorization"] = f"Bearer {key}"
    with keyed_app.state.session_factory() as db:
        for project in ("bank", "loans"):
            db.add(Project(id=project, name=project, config_json="{}", current_version=1))
        db.commit()
        db.add_all(PcAssignment(pc_id=made["pc_id"], project_id=p) for p in ("bank", "loans"))
        db.commit()

    class World:
        app, pc_id, admin_client, pc_client = keyed_app, made["pc_id"], admin, pc

        @staticmethod
        def meta(doc="doc-1", project="bank", sha=SHA, **over):
            body = {"project_id": project, "batch_id": "b-1", "page_count": 3, "created_at": "2026-10-17T09:30:00Z",
                    "validation_status": "approved", "file_name": "kamal-553216789V.pdf", "size": 7, "sha256": sha,
                    "delivered_to": ["folder: D:/out"], "delivery_status": "delivered", **over}
            return pc.put(f"/pcs/{made['pc_id']}/documents/{doc}/meta", json=body)

        @staticmethod
        def index(doc="doc-1", fields=None, **over):
            body = {"fields": FIELDS if fields is None else fields, "validation_status": "approved", **over}
            return pc.put(f"/pcs/{made['pc_id']}/documents/{doc}/index", json=body)

    return World


def search(client, **params):
    r = client.get("/admin/documents", params=params)
    assert r.status_code == 200, r.text
    return r.json()


# -- sending ----------------------------------------------------------------------------------------------------
def test_a_documents_details_and_fields_arrive_and_can_be_found(world):
    assert world.meta().json() == {"status": "stored", "file_state": "metadata_only"}
    assert world.index().json() == {"status": "stored", "revision": 1}
    found = search(world.admin_client, q=NIC)
    assert found["total"] == 1
    item = found["items"][0]
    assert item["pc_name"] == "Galle desk" and item["fields"]["full_name"] == "Kamal Perera" and item["revision"] == 1


def test_sending_the_same_thing_again_changes_nothing(world):
    world.meta()
    world.index()
    assert world.meta().json()["status"] == "unchanged"
    assert world.index().json() == {"status": "unchanged", "revision": 1}
    assert search(world.admin_client, q=NIC)["total"] == 1


def test_changed_fields_become_the_next_revision_and_the_history_shows_what_changed(world):
    world.meta()
    world.index()
    assert world.index(fields={**FIELDS, "full_name": "Kamal A Perera"}).json() == {"status": "stored", "revision": 2}
    doc_id = search(world.admin_client, q=NIC)["items"][0]["id"]
    detail = world.admin_client.get(f"/admin/documents/{doc_id}").json()
    assert detail["revision"] == 2 and detail["fields"]["full_name"] == "Kamal A Perera"
    newest, first = detail["history"]
    assert newest["is_current"] and not first["is_current"]
    assert newest["changes"] == [{"field": "full_name", "before": "Kamal Perera", "after": "Kamal A Perera"}]


def test_a_re_exported_pdf_becomes_the_current_file_and_the_old_one_is_kept(world):
    world.meta()
    new_sha = hashlib.sha256(b"pdf two").hexdigest()
    assert world.meta(sha=new_sha, size=9).json()["status"] == "updated"
    world.index()
    doc_id = search(world.admin_client, q=NIC)["items"][0]["id"]
    files = world.admin_client.get(f"/admin/documents/{doc_id}").json()["files"]
    assert [(f["sha256"], f["is_current"]) for f in files] == [(new_sha, True), (SHA, False)]
    assert files[0]["file_state"] == "metadata_only" and files[0]["delivered_to"] == ["folder: D:/out"]
    assert files[0]["file_name"] == "kamal-553216789V.pdf"


def test_the_fields_cannot_arrive_before_the_details(world):
    assert world.index(doc="never-seen").status_code == 409


def test_nothing_is_accepted_without_the_data_key(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'n.db'}", session_secret="x" * 40, cookie_secure=False))
    create_admin(app.state.session_factory, "root", "correct horse battery")
    made = login(app).post("/admin/pcs", json={"display_name": "P"}).json()
    pc = TestClient(app)
    key = pc.post("/pcs/register", json={"code": made["enrollment_code"]}).json()["api_key"]
    r = pc.put(f"/pcs/{made['pc_id']}/documents/d/meta", headers={"Authorization": f"Bearer {key}"},
               json={"project_id": "p", "batch_id": "b", "page_count": 1, "created_at": "2026-10-17T09:30:00Z"})
    assert r.status_code == 503


# -- who may send ---------------------------------------------------------------------------------------------------
def test_another_stations_key_cannot_write_here(world):
    other = world.admin_client.post("/admin/pcs", json={"display_name": "Other"}).json()
    c = TestClient(world.app)
    key = c.post("/pcs/register", json={"code": other["enrollment_code"]}).json()["api_key"]
    r = c.put(f"/pcs/{world.pc_id}/documents/doc-1/meta", headers={"Authorization": f"Bearer {key}"},
              json={"project_id": "p", "batch_id": "b", "page_count": 1, "created_at": "2026-10-17T09:30:00Z"})
    assert r.status_code == 403
    assert TestClient(world.app).put(f"/pcs/{world.pc_id}/documents/doc-1/index", json={"fields": {}}).status_code == 401


@pytest.mark.parametrize("body", [
    {"project_id": "p", "batch_id": "b", "page_count": -1, "created_at": "2026-10-17T09:30:00Z"},
    {"project_id": "p", "batch_id": "b", "page_count": 1, "created_at": "not a date"},
    {"project_id": "p", "batch_id": "b", "page_count": 1, "created_at": "2099-01-01T00:00:00Z"},
    {"project_id": "p q", "batch_id": "b", "page_count": 1, "created_at": "2026-10-17T09:30:00Z"},
    {"project_id": "p", "batch_id": "b", "page_count": 1, "created_at": "2026-10-17T09:30:00Z", "sha256": "xyz"},
])
def test_bad_details_are_refused(world, body):
    assert world.pc_client.put(f"/pcs/{world.pc_id}/documents/doc-1/meta", json=body).status_code == 422


def test_bad_fields_are_refused(world):
    world.meta()
    assert world.index(fields={"bad name!": "x"}).status_code == 422
    assert world.index(fields={"nic": "x" * 2001}).status_code == 422
    assert world.pc_client.put(f"/pcs/{world.pc_id}/documents/..%2Fx/meta", json={}).status_code in (404, 422)


def test_a_station_cannot_file_documents_under_a_project_it_was_not_given(world):
    with world.app.state.session_factory() as db:
        db.add(Project(id="secret", name="secret", config_json="{}", current_version=1))
        db.commit()
    assert world.meta(project="secret").status_code == 422
    world.meta()                                                       # filed under its own project
    assert world.meta(project="secret").status_code == 422             # nor moved into another one later
    assert search(world.admin_client, project="secret")["total"] == 0


# -- encryption at rest ----------------------------------------------------------------------------------------------
def test_nothing_personal_is_readable_in_the_database(world):
    world.meta()
    world.index()
    with world.app.state.engine.connect() as c:
        dump = " ".join(str(v) for t in ("index_revisions", "document_files", "index_lookup", "documents", "audit_events")
                        for row in c.execute(text(f"select * from {t}")) for v in row)
    for secret in (NIC, "Kamal", "1234567890", "kamal-553216789V", "D:/out", "Galle"):
        assert secret not in dump, secret


def test_a_tampered_row_is_reported_unreadable_not_shown_wrong(world):
    world.meta()
    world.index()
    with world.app.state.engine.begin() as c:
        c.execute(text("update index_revisions set fields_enc = '1:AAAA' || substr(fields_enc, 7)"))
    doc_id = search(world.admin_client, q=NIC)["items"][0]["id"]
    assert search(world.admin_client, q=NIC)["items"][0]["fields"] is None
    detail = world.admin_client.get(f"/admin/documents/{doc_id}").json()
    assert detail["fields_unreadable"] is True and detail["fields"] is None


def test_the_wrong_key_finds_nothing_and_shows_nothing(world, tmp_path):
    world.meta()
    world.index()
    from central.crypto import DataKey
    other = create_app(Settings(database_url=world.app.state.settings.database_url, session_secret="x" * 40,
                                cookie_secure=False, data_key=DataKey(generate_key())))
    admin = login(other)
    assert search(admin, q=NIC)["total"] == 0
    assert admin.get("/admin/documents", params={"project": "bank"}).json()["items"][0]["fields"] is None


# -- searching -------------------------------------------------------------------------------------------------------------
def test_an_identifier_matches_however_it_is_typed_and_in_any_field(world):
    world.meta()
    world.index()
    assert search(world.admin_client, q="553 216-789v")["total"] == 1
    assert search(world.admin_client, field="nic", value=NIC.lower())["total"] == 1
    assert search(world.admin_client, field="account_no", value="1234567890")["total"] == 1
    assert search(world.admin_client, field="account_no", value=NIC)["total"] == 0          # right value, wrong field
    assert search(world.admin_client, q="000000000V")["total"] == 0


def test_a_search_needs_enough_to_go_on_and_both_halves_of_a_field_search(world):
    assert world.admin_client.get("/admin/documents", params={"q": "ab"}).status_code == 422
    assert world.admin_client.get("/admin/documents", params={"field": "nic"}).status_code == 422
    assert world.admin_client.get("/admin/documents", params={"date_from": "yesterday"}).status_code == 422


def test_filters_by_project_station_batch_status_and_date_and_paging(world):
    for i in range(5):
        world.meta(doc=f"d{i}", project="bank" if i < 3 else "loans", sha=hashlib.sha256(str(i).encode()).hexdigest())
        world.index(doc=f"d{i}", fields={"nic": f"55321678{i}V"})
    a = world.admin_client
    assert search(a, project="bank")["total"] == 3 and search(a, project="loans")["total"] == 2
    assert search(a, pc=world.pc_id)["total"] == 5 and search(a, pc="nobody")["total"] == 0
    assert search(a, batch="b-1")["total"] == 5 and search(a, status="rejected")["total"] == 0
    assert search(a, date_from="2026-10-17", date_to="2026-10-17")["total"] == 5
    assert search(a, date_from="2026-10-18")["total"] == 0
    page = search(a, limit=2, offset=4)
    assert page["total"] == 5 and len(page["items"]) == 1
    names = {p["name"] for p in a.get("/admin/documents/filters").json()["projects"]}
    assert names == {"bank", "loans"}


# -- roles -----------------------------------------------------------------------------------------------------------------
@pytest.fixture()
def searcher(world):
    world.meta(doc="in-scope", project="bank")
    world.index(doc="in-scope", fields={"nic": NIC})
    world.meta(doc="out-of-scope", project="loans", sha=hashlib.sha256(b"other").hexdigest())
    world.index(doc="out-of-scope", fields={"nic": "199912345678"})
    made = world.admin_client.post("/admin/users", json={"username": "finder", "password": "a long passphrase here",
                                                        "role": "searcher"})
    assert made.status_code == 201, made.text
    uid = made.json()["id"]
    assert world.admin_client.put(f"/admin/users/{uid}/projects", json={"project_ids": ["bank"]}).status_code == 200
    return login(world.app, "finder", "a long passphrase here"), uid


def test_a_searcher_sees_only_their_projects(world, searcher):
    client, _ = searcher
    assert [i["project_id"] for i in search(client)["items"]] == ["bank"]
    assert search(client, q="199912345678")["total"] == 0                       # exists, but not theirs
    assert search(client, q=NIC)["total"] == 1
    other = search(world.admin_client, q="199912345678")["items"][0]["id"]
    assert client.get(f"/admin/documents/{other}").status_code == 404           # looks the same as absent
    assert [p["id"] for p in client.get("/admin/documents/filters").json()["projects"]] == ["bank"]


def test_a_searcher_with_no_projects_sees_nothing(world, searcher):
    client, uid = searcher
    world.admin_client.put(f"/admin/users/{uid}/projects", json={"project_ids": []})
    assert search(client)["total"] == 0


def test_a_searcher_can_reach_nothing_else_and_change_nothing(world, searcher):
    client, uid = searcher
    for path in ("/admin/fleet", "/admin/users", "/admin/pcs", "/admin/audit", "/admin/alerts", "/admin/webhook",
                 "/admin/documents/access-log"):
        assert client.get(path).status_code == 403, path
    assert client.post("/admin/pcs", json={"display_name": "x"}).status_code == 403
    assert client.get("/admin/me").status_code == 200


def test_the_scope_belongs_to_searchers_and_is_cleared_when_the_role_changes(world, searcher):
    _, uid = searcher
    root_id = next(u["id"] for u in world.admin_client.get("/admin/users").json() if u["username"] == "root")
    assert world.admin_client.put(f"/admin/users/{root_id}/projects", json={"project_ids": ["bank"]}).status_code == 409
    listed = {u["username"]: u for u in world.admin_client.get("/admin/users").json()}
    assert listed["finder"]["projects"] == ["bank"] and "projects" not in listed["root"]
    world.admin_client.post(f"/admin/users/{uid}/role", json={"role": "searcher"})
    world.admin_client.post(f"/admin/users/{uid}/role", json={"role": "auditor"})
    world.admin_client.post(f"/admin/users/{uid}/role", json={"role": "searcher"})
    assert world.admin_client.get("/admin/users").json()[0]["projects"] == []


def test_an_auditor_sees_every_project_read_only(world, searcher):
    world.admin_client.post("/admin/users", json={"username": "watcher", "password": "a long passphrase here",
                                                  "role": "auditor"})
    auditor = login(world.app, "watcher", "a long passphrase here")
    assert search(auditor)["total"] == 2
    assert auditor.get("/admin/documents/access-log").status_code == 200


# -- the access log --------------------------------------------------------------------------------------------------------
def test_searches_and_views_are_logged_without_the_value_searched_for(world):
    world.meta()
    world.index()
    item = search(world.admin_client, q=NIC)["items"][0]
    world.admin_client.get(f"/admin/documents/{item['id']}")
    log = world.admin_client.get("/admin/documents/access-log").json()["items"]
    assert [(e["action"], e["user"]) for e in log] == [("view", "root"), ("search", "root")]
    assert log[0]["document_id"] == item["id"] and log[1]["detail"] == {"filters": ["any_field"], "found": 1}
    assert NIC not in str(log)
