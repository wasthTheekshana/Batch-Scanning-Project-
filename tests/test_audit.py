import json

import pytest

from data.audit import AuditLogger
from data.db import Database


@pytest.fixture()
def audit(tmp_path):
    db = Database(tmp_path / "a.db")
    logger = AuditLogger(db, tmp_path / "logs")
    yield db, logger, tmp_path
    logger.close()
    db.close()


def test_log_writes_db_and_file(audit):
    db, logger, tmp = audit
    logger.log("scan", "operator1", pages=4)
    row = db.conn.execute("SELECT * FROM audit_log").fetchone()
    assert row["action"] == "scan" and row["user"] == "operator1"
    assert json.loads(row["detail"])["pages"] == 4

    lines = (tmp / "logs" / "audit.log").read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[0])
    assert entry["action"] == "scan" and entry["detail"]["pages"] == 4


def test_document_audit_trail_grows(audit):
    db, logger, _ = audit
    db.insert_project("p1", "P", {})
    db.insert_batch("b1", "p1", "op")
    db.insert_document("d1", "b1", "p1")
    logger.log("qc", "system", document_id="d1", score=88)
    logger.log("export", "system", document_id="d1", path="out.pdf")
    trail = json.loads(db.get_document("d1")["audit_trail"])
    assert [t["action"] for t in trail] == ["qc", "export"]
    assert trail[0]["detail"]["score"] == 88
