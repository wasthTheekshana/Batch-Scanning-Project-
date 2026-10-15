"""The PC database carries a schema version so later releases can change it
without losing a bank's stored documents."""
import sqlite3

import pytest

import data.db as dbmod
from data.db import Database, DatabaseTooNew, MigrationError, SCHEMA_VERSION


def version(path):
    with sqlite3.connect(path) as c:
        return c.execute("PRAGMA user_version").fetchone()[0]


def test_a_new_database_is_stamped_with_the_current_version(tmp_path):
    db = Database(tmp_path / "a.db")
    db.close()
    assert version(tmp_path / "a.db") == SCHEMA_VERSION


def test_a_database_made_before_versioning_is_adopted_with_its_data(tmp_path):
    path = tmp_path / "old.db"
    db = Database(path)
    db.insert_project("p1", "Keep me", {"x": 1})
    db.close()
    with sqlite3.connect(path) as c:           # what an older release left behind
        c.execute("PRAGMA user_version = 0")
    db = Database(path)
    assert db.get_project("p1")["name"] == "Keep me"
    db.close()
    assert version(path) == SCHEMA_VERSION


def _add_migration(monkeypatch, number, sql):
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", number)
    monkeypatch.setattr(dbmod, "_MIGRATIONS",
                        list(dbmod._MIGRATIONS) + [(number, sql)])


def test_a_pending_migration_runs_once_and_in_order(tmp_path, monkeypatch):
    path = tmp_path / "m.db"
    Database(path).close()
    base = SCHEMA_VERSION
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", base + 2)
    monkeypatch.setattr(dbmod, "_MIGRATIONS", [
        (base + 1, "CREATE TABLE step1 (n INTEGER); INSERT INTO step1 VALUES (1);"),
        (base + 2, "CREATE TABLE step2 (n INTEGER); "
                   "INSERT INTO step2 SELECT n + 1 FROM step1;"),
    ])
    db = Database(path)
    assert db.conn.execute("SELECT n FROM step2").fetchone()[0] == 2
    db.close()
    assert version(path) == base + 2
    db = Database(path)                         # opening again changes nothing
    assert db.conn.execute("SELECT COUNT(*) FROM step1").fetchone()[0] == 1
    db.close()


def test_a_failing_migration_changes_nothing_and_leaves_a_backup(tmp_path, monkeypatch):
    path = tmp_path / "f.db"
    db = Database(path)
    db.insert_project("p1", "Precious", {})
    db.close()
    before = version(path)
    _add_migration(monkeypatch, before + 1,
                   "CREATE TABLE half_done (n INTEGER); "
                   "INSERT INTO table_that_does_not_exist VALUES (1);")
    with pytest.raises(MigrationError):
        Database(path)
    assert version(path) == before
    with sqlite3.connect(path) as c:
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
        assert "half_done" not in names           # rolled back, not half applied
        assert c.execute("SELECT name FROM projects").fetchone()[0] == "Precious"
    backups = list(tmp_path.glob("f.db.before-v*.bak"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as c:
        assert c.execute("SELECT name FROM projects").fetchone()[0] == "Precious"


def test_a_database_from_a_newer_release_is_refused(tmp_path):
    path = tmp_path / "new.db"
    Database(path).close()
    with sqlite3.connect(path) as c:
        c.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")
    with pytest.raises(DatabaseTooNew):
        Database(path)


def test_no_backup_is_made_when_nothing_is_pending(tmp_path):
    path = tmp_path / "q.db"
    Database(path).close()
    Database(path).close()
    assert not list(tmp_path.glob("*.bak"))
