import copy

import pytest

from core.profile_manager import (DEFAULT_PROJECT, ProfileManager,
                                  ProjectConfigError, is_safe_project_id)
from data.db import Database


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "c.db")
    yield d
    d.close()


@pytest.fixture()
def pm(tmp_path, db):
    return ProfileManager(tmp_path / "projects", db)


def _cfg(project_id="11111111-2222-3333-4444-555555555555", name="Invoices"):
    cfg = copy.deepcopy(DEFAULT_PROJECT)
    cfg.update(project_id=project_id, name=name, version=7,
               created="2026-10-09T00:00:00+00:00")
    cfg["rules"]["rule_set_id"] = "rs-1"
    return cfg


# ---- db bookkeeping -----------------------------------------------------------
def test_central_project_upsert_get_list(db):
    assert db.get_central_project("p1") is None
    db.set_central_project("p1", 3)
    db.set_central_project("p2", 1)
    row = db.get_central_project("p1")
    assert row["central_version"] == 3 and row["managed"] == 1 and row["applied_at"]
    db.set_central_project("p1", 4)
    assert db.get_central_project("p1")["central_version"] == 4
    assert {r["project_id"] for r in db.list_central_projects()} == {"p1", "p2"}


def test_unmanage_one_and_all(db):
    db.set_central_project("p1", 1)
    db.set_central_project("p2", 1)
    db.unmanage_central_project("p1")
    assert db.get_central_project("p1")["managed"] == 0
    assert [r["project_id"] for r in db.list_central_projects(managed_only=True)] == ["p2"]
    db.unmanage_all_central_projects()
    assert db.list_central_projects(managed_only=True) == []
    assert len(db.list_central_projects()) == 2  # rows kept, just unmanaged


# ---- adopt ---------------------------------------------------------------------
def test_adopt_new_project_keeps_id_starts_at_version_1(pm, db, tmp_path):
    out = pm.adopt(_cfg())
    assert out["project_id"] == "11111111-2222-3333-4444-555555555555"
    assert out["version"] == 1
    assert (tmp_path / "projects" / "11111111-2222-3333-4444-555555555555.json").exists()
    row = db.get_project(out["project_id"])
    assert row["name"] == "Invoices" and row["version"] == 1


def test_adopt_existing_project_bumps_version_and_archives(pm, db, tmp_path):
    pm.adopt(_cfg())
    changed = _cfg()
    changed["ocr"]["lang"] = "deu"
    out = pm.adopt(changed)
    assert out["version"] == 2 and out["ocr"]["lang"] == "deu"
    assert pm.list_versions(out["project_id"]) == [1]
    assert db.get_project(out["project_id"])["version"] == 2


def test_adopt_when_db_row_exists_but_file_missing(pm, db, tmp_path):
    db.insert_project("aaaa-bbbb", "Old", _cfg("aaaa-bbbb", "Old"), version=5)
    out = pm.adopt(_cfg("aaaa-bbbb", "New"))
    assert out["version"] == 1
    assert db.get_project("aaaa-bbbb")["name"] == "New"


@pytest.mark.parametrize("bad_id", ["../evil", "a/b", "a\\b", "", "x" * 37,
                                    "has space", "..", "a.b"])
def test_adopt_rejects_unsafe_ids_and_writes_nothing(pm, tmp_path, bad_id):
    with pytest.raises(ProjectConfigError):
        pm.adopt(_cfg(project_id=bad_id))
    assert list((tmp_path).rglob("evil*")) == []
    assert list((tmp_path / "projects").glob("*.json")) == []


def test_adopt_when_file_exists_but_db_row_missing_inserts_row(pm, db, tmp_path):
    first = pm.adopt(_cfg())
    pid = first["project_id"]
    db.conn.execute("DELETE FROM projects WHERE project_id=?", (pid,))
    db.conn.commit()
    assert db.get_project(pid) is None
    out = pm.adopt(_cfg(name="Renamed"))
    assert out["version"] == 2
    row = db.get_project(pid)
    assert row is not None and row["name"] == "Renamed" and row["version"] == 2


_BAD_IDS = ["abc\n", 123, None, ["a"], "CON", "nul", "Com1", "LPT9"]
_GOOD_IDS = ["x" * 36, "console", "com10"]


@pytest.mark.parametrize("bad_id", _BAD_IDS)
def test_is_safe_project_id_rejects(bad_id):
    assert is_safe_project_id(bad_id) is False


@pytest.mark.parametrize("good_id", _GOOD_IDS)
def test_is_safe_project_id_accepts(good_id):
    assert is_safe_project_id(good_id) is True


@pytest.mark.parametrize("bad_id", _BAD_IDS)
def test_adopt_rejects_more_unsafe_ids(pm, tmp_path, bad_id):
    with pytest.raises(ProjectConfigError):
        pm.adopt(_cfg(project_id=bad_id))
    assert list((tmp_path / "projects").glob("*.json")) == []


@pytest.mark.parametrize("good_id", _GOOD_IDS)
def test_adopt_accepts_boundary_ids(pm, tmp_path, good_id):
    out = pm.adopt(_cfg(project_id=good_id))
    assert out["project_id"] == good_id
    assert (tmp_path / "projects" / f"{good_id}.json").exists()


def test_adopt_rejects_invalid_config(pm):
    bad = _cfg()
    del bad["scanner"]
    with pytest.raises(ProjectConfigError):
        pm.adopt(bad)


# ---- schema migration ----------------------------------------------------------
def test_existing_db_without_central_table_gets_it_on_open(tmp_path):
    path = tmp_path / "old.db"
    d = Database(path)
    d.conn.execute("DROP TABLE central_projects")
    d.conn.commit()
    d.close()

    reopened = Database(path)
    try:
        assert reopened.list_central_projects() == []
        reopened.set_central_project("p1", 2)
        assert reopened.get_central_project("p1")["central_version"] == 2
    finally:
        reopened.close()


def test_adopt_row_insert_branch_keeps_status(pm, db):
    pid = pm.adopt(_cfg())["project_id"]
    db.conn.execute("DELETE FROM projects WHERE project_id=?", (pid,))
    db.conn.commit()
    archived = _cfg()
    archived["status"] = "archived"
    pm.adopt(archived)
    assert db.get_project(pid)["status"] == "archived"
