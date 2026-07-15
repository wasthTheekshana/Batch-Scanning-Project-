import sqlite3
import zipfile

import pytest

from data.backup import BackupService, RestoreError, restore_backup
from data.db import Database

KEY = b"k" * 32


@pytest.fixture()
def env(tmp_path):
    db = Database(tmp_path / "live.db")
    db.insert_project("p1", "Backed Up Project", {"name": "Backed Up Project"})
    db.insert_batch("b1", "p1", "op")
    db.insert_document("d1", "b1", "p1", qc_score=91)
    store = tmp_path / "delivered"
    store.mkdir()
    (store / "doc1.pdf").write_bytes(b"%PDF-fake")
    yield db, store, tmp_path
    db.close()


def test_backup_restore_round_trip(env):
    db, store, tmp = env
    svc = BackupService(db, [store], tmp / "backups", KEY)
    archive = svc.run_backup()
    assert archive.exists() and svc.last_result["path"] == str(archive)

    restored_db = restore_backup(archive, tmp / "restore", KEY)
    conn = sqlite3.connect(str(restored_db))
    try:
        name = conn.execute(
            "SELECT name FROM projects WHERE project_id='p1'").fetchone()[0]
        score = conn.execute(
            "SELECT qc_score FROM documents WHERE document_id='d1'").fetchone()[0]
    finally:
        conn.close()
    assert name == "Backed Up Project" and score == 91
    assert (tmp / "restore" / "files" / "delivered" / "doc1.pdf"
            ).read_bytes() == b"%PDF-fake"


def test_archive_is_encrypted(env):
    db, store, tmp = env
    archive = BackupService(db, [store], tmp / "backups", KEY).run_backup()
    # not a readable zip without the key
    assert not zipfile.is_zipfile(archive)
    with pytest.raises(RestoreError):
        restore_backup(archive, tmp / "r2", b"wrong-key".ljust(32, b"x"))


def test_encrypted_db_backup(tmp_path):
    db = Database(tmp_path / "enc.db", key=b"s" * 32)
    db.insert_project("p1", "Enc", {})
    svc = BackupService(db, [], tmp_path / "backups", KEY)
    archive = svc.run_backup()
    restored = restore_backup(archive, tmp_path / "restore", KEY)
    conn = sqlite3.connect(str(restored))
    assert conn.execute("SELECT name FROM projects").fetchone()[0] == "Enc"
    conn.close()
    db.close()


def test_retention_prunes_old_archives(env):
    db, store, tmp = env
    svc = BackupService(db, [store], tmp / "backups", KEY, keep=2)
    paths = []
    import time
    for _ in range(3):
        paths.append(svc.run_backup())
        time.sleep(1.1)  # distinct timestamps
    remaining = sorted((tmp / "backups").glob("*.dokbak"))
    assert len(remaining) == 2
    assert paths[0] not in remaining
