import json

import pytest

from data.db import Database

TABLES = {"projects", "batches", "documents", "users", "audit_log", "jobs"}


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "test.db")
    yield d
    d.close()


def test_schema_creates_all_tables(db):
    rows = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    names = {r["name"] for r in rows}
    assert TABLES <= names


def test_wal_mode_enabled(db):
    mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_init_schema_idempotent(tmp_path):
    path = tmp_path / "x.db"
    d1 = Database(path)
    d1.insert_project("p1", "Test", {"name": "Test"})
    d1.close()
    d2 = Database(path)  # re-runs init_schema
    assert d2.get_project("p1")["name"] == "Test"
    d2.close()


def test_document_round_trip(db):
    db.insert_project("p1", "Proj", {"name": "Proj"})
    db.insert_batch("b1", "p1", "operator1")
    db.insert_document("d1", "b1", "p1", page_range="1-3")
    db.update_document("d1",
                       qc_score=87,
                       qc_detail={"punch_hole": 0, "rotation": -5},
                       extracted_fields={"invoice_no": "INV-42"},
                       validation_status="auto_approved")
    doc = db.get_document("d1")
    assert doc["qc_score"] == 87
    assert json.loads(doc["qc_detail"])["rotation"] == -5
    assert json.loads(doc["extracted_fields"])["invoice_no"] == "INV-42"
    assert doc["delivery_status"] == "pending"
    assert doc["validation_status"] == "auto_approved"


def test_list_documents_by_batch(db):
    db.insert_project("p1", "Proj", {})
    db.insert_batch("b1", "p1", "op")
    db.insert_document("d1", "b1", "p1")
    db.insert_document("d2", "b1", "p1")
    assert {d["document_id"] for d in db.list_documents("b1")} == {"d1", "d2"}


def test_audit_append(db):
    db.append_audit(None, "admin", "login", {"ok": True})
    db.append_audit("d1", "op", "qc_review", {"score": 70})
    rows = db.conn.execute("SELECT * FROM audit_log ORDER BY log_id").fetchall()
    assert len(rows) == 2
    assert rows[0]["action"] == "login"
    assert json.loads(rows[1]["detail"])["score"] == 70


def test_users(db):
    db.insert_user("u1", "alice", "hash123", "operator")
    u = db.get_user("alice")
    assert u["role"] == "operator" and u["active"] == 1


def test_job_recovery(db):
    db.insert_project("p1", "Proj", {})
    db.insert_batch("b1", "p1", "op")
    db.upsert_job("j1", "b1", "p1", 1, "queued", "c:/staging/1.png")
    db.upsert_job("j2", "b1", "p1", 2, "in_progress", "c:/staging/2.png")
    db.upsert_job("j3", "b1", "p1", 3, "complete", "c:/staging/3.png")
    incomplete = {j["job_id"] for j in db.get_incomplete_jobs()}
    assert incomplete == {"j1", "j2"}
    db.mark_job("j1", "complete")
    assert {j["job_id"] for j in db.get_incomplete_jobs()} == {"j2"}
