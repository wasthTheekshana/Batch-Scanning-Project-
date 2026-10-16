"""Concurrency of the central service: things that must hold when several
admins and PCs act at the same moment.

By default these run on the throw-away SQLite file the other tests use. To run
them against a real PostgreSQL (the production database), point
CENTRAL_TEST_DATABASE_URL at an EMPTY, disposable database, e.g.

    set CENTRAL_TEST_DATABASE_URL=postgresql+psycopg://user:pw@localhost/dokcentral_test
    pytest tests/central/test_concurrency.py

The tables are created for the run and every row is removed afterwards. They
were written to be run that way before a production deploy; SQLite alone does
not prove row-level locking.
"""
import os
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from central.app import create_app
from central.manage import create_admin
from central.models import AdminUser, ConfigVersion, EnrollmentCode, Pc, Project
from central.settings import Settings

GOOD = "a very long passphrase"
THREADS = 8
POSTGRES_URL = os.environ.get("CENTRAL_TEST_DATABASE_URL")


@pytest.fixture()
def cc_app(tmp_path):
    url = POSTGRES_URL or f"sqlite:///{tmp_path / 'concurrent.db'}"
    app = create_app(Settings(database_url=url, session_secret="x" * 40, cookie_secure=False))
    with TestClient(app):                      # runs start-up (creates the tables)
        pass
    yield app
    if POSTGRES_URL:                           # leave the disposable database empty
        with app.state.session_factory() as db:
            for model in (ConfigVersion, EnrollmentCode, Project, Pc, AdminUser):
                db.execute(delete(model))
            db.commit()


def sign_in(app):
    client = TestClient(app)
    r = client.post("/admin/login", json={"username": "root", "password": "correct horse battery"})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return client


def at_once(n, work):
    """Run work(i) in n threads released together; returns their results."""
    barrier = threading.Barrier(n)
    results = [None] * n

    def run(i):
        try:
            barrier.wait(timeout=30)
            results[i] = work(i)
        except Exception as e:                 # a crash is a result too
            results[i] = e

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    return results


def test_an_enrollment_code_can_be_used_by_only_one_pc_at_a_time(cc_app):
    create_admin(cc_app.state.session_factory, "root", "correct horse battery")
    admin = sign_in(cc_app)
    code = admin.post("/admin/pcs", json={"display_name": "PC-1"}).json()["enrollment_code"]
    results = at_once(THREADS, lambda i: TestClient(cc_app).post(
        "/pcs/register", json={"code": code}).status_code)
    assert sorted(results).count(200) == 1, results            # exactly one wins
    assert all(r in (200, 400) for r in results), results      # the rest are refused, never a 500


def test_saving_one_project_at_once_never_duplicates_or_skips_a_version(cc_app, project_config):
    create_admin(cc_app.state.session_factory, "root", "correct horse battery")
    admin = sign_in(cc_app)
    pid = project_config["project_id"]
    assert admin.put(f"/admin/projects/{pid}", json={"name": "P", "config": project_config}).status_code == 200

    def save(i):
        client = sign_in(cc_app)
        cfg = {**project_config, "ocr": {**project_config["ocr"], "confidence_threshold": 0.5 + i / 100}}
        return client.put(f"/admin/projects/{pid}", json={"name": "P", "config": cfg}).status_code

    codes = at_once(THREADS, save)
    assert all(c in (200, 409) for c in codes), codes          # saved, or told to retry: never a crash
    with cc_app.state.session_factory() as db:
        versions = sorted(v.version for v in db.scalars(
            select(ConfigVersion).where(ConfigVersion.owner_id == pid)))
        current = db.get(Project, pid).current_version
    assert versions == list(range(1, len(versions) + 1)), versions   # no gap, no duplicate
    assert current == len(versions) == 1 + codes.count(200)


def test_creating_the_same_project_at_once_makes_one_project(cc_app, project_config):
    create_admin(cc_app.state.session_factory, "root", "correct horse battery")
    pid = project_config["project_id"]

    def create(i):
        return sign_in(cc_app).put(f"/admin/projects/{pid}",
                                   json={"name": "P", "config": project_config}).status_code

    codes = at_once(THREADS, create)
    assert all(c in (200, 409) for c in codes), codes
    with cc_app.state.session_factory() as db:
        assert len(db.scalars(select(Project).where(Project.id == pid)).all()) == 1
        versions = [v.version for v in db.scalars(select(ConfigVersion).where(ConfigVersion.owner_id == pid))]
    assert versions == [1]                                     # the same config saved many times is one version


def test_two_admins_disabling_each_other_leave_someone_able_to_sign_in(cc_app):
    create_admin(cc_app.state.session_factory, "root", "correct horse battery")
    create_admin(cc_app.state.session_factory, "second", "correct horse battery")
    with cc_app.state.session_factory() as db:
        ids = {u.username: u.id for u in db.scalars(select(AdminUser))}

    def disable(i):
        who, other = ("root", "second") if i % 2 == 0 else ("second", "root")
        client = TestClient(cc_app)
        r = client.post("/admin/login", json={"username": who, "password": "correct horse battery"})
        if r.status_code != 200:
            return r.status_code
        client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        return client.post(f"/admin/users/{ids[other]}/disable").status_code

    codes = at_once(4, disable)
    assert all(not isinstance(c, Exception) for c in codes), codes
    with cc_app.state.session_factory() as db:
        enabled = [u.username for u in db.scalars(select(AdminUser))
                   if not (u.locked_until and u.locked_until.year >= 9999)]
    assert enabled, "every admin was disabled"                 # at least one can still sign in


def test_many_heartbeats_at_once_all_succeed(cc_app):
    create_admin(cc_app.state.session_factory, "root", "correct horse battery")
    admin = sign_in(cc_app)
    d = admin.post("/admin/pcs", json={"display_name": "PC-1"}).json()
    key = TestClient(cc_app).post("/pcs/register", json={"code": d["enrollment_code"]}).json()["api_key"]
    results = at_once(THREADS, lambda i: TestClient(cc_app).post(
        f"/pcs/{d['pc_id']}/heartbeat", headers={"Authorization": f"Bearer {key}"}).status_code)
    assert results == [204] * THREADS, results
