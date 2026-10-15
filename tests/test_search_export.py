"""Downloading search results as CSV."""
import csv
import io

import pytest

from core.rbac import Session, hash_password
from core.search import SearchQuery
from core.search_export import FIXED, build_rows, export_csv
from data.db import Database
from ui.search_panel import SearchPanel


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "se.db")
    d.insert_project("p1", "Bank", {})
    d.insert_batch("b1", "p1", "alice")
    d.insert_batch("b2", "p1", "bob")
    rows = [("d1", "b1", {"barcode": "CIF-1", "doc_types": "NIC, Application",
                          "nic_number": "199012345678", "applicant_name": "Nimal, \"N\" Perera"}),
            ("d2", "b2", {"barcode": "CIF-2", "nic_number": "=cmd|' /C calc'!A0"}),
            ("d3", "b1", {"barcode": "CIF-3", "document_id": "evil"})]
    for doc_id, batch, fields in rows:
        d.insert_document(doc_id, batch, "p1", page_range="1-2", qc_score=90,
                          validation_status="auto_approved", extracted_fields=fields)
        d.update_document(doc_id, delivery_status="delivered_local",
                          storage_path=f"/out/{doc_id}.pdf")
    yield d
    Session.logout()
    d.close()


def parse(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def test_every_match_is_written_with_fixed_columns_then_the_index_fields(db, tmp_path):
    out = tmp_path / "r.csv"
    count, truncated = export_csv(db, SearchQuery(), out)
    rows = parse(out)
    assert count == 3 and not truncated and len(rows) == 3
    assert list(rows[0])[:len(FIXED)] == FIXED
    assert {"nic_number", "applicant_name"} <= set(rows[0])
    by = {r["document_id"]: r for r in rows}
    assert by["d1"]["barcode"] == "CIF-1" and by["d1"]["document_types"] == "NIC, Application"
    assert by["d1"]["pdf_file"] == "d1.pdf" and by["d1"]["project"] == "Bank"
    assert by["d1"]["applicant_name"] == 'Nimal, "N" Perera'        # quoting survives
    assert by["d2"]["applicant_name"] == ""                          # blank where absent


def test_the_export_follows_the_search_filters(db, tmp_path):
    count, _ = export_csv(db, SearchQuery(text="199012"), tmp_path / "r.csv")
    assert count == 1 and parse(tmp_path / "r.csv")[0]["document_id"] == "d1"
    count, _ = export_csv(db, SearchQuery(only_operator="bob"), tmp_path / "b.csv")
    assert [r["document_id"] for r in parse(tmp_path / "b.csv")] == ["d2"]


def test_formulas_are_neutralised_and_a_field_cannot_overwrite_a_fixed_column(db, tmp_path):
    export_csv(db, SearchQuery(), tmp_path / "r.csv")
    by = {r["document_id"]: r for r in parse(tmp_path / "r.csv")}
    assert by["d2"]["nic_number"].startswith("'=")
    assert "d3" in by and by["d3"]["document_id"] == "d3"           # not "evil"


def test_more_than_one_page_of_results_is_all_exported(db, tmp_path, monkeypatch):
    import core.search_export as se
    monkeypatch.setattr(se, "MAX_LIMIT", 2)                          # pages of 2: 3 docs = 2 pages
    cols, rows, truncated = build_rows(db, SearchQuery())
    assert len(rows) == 3 and not truncated


def test_the_row_cap_truncates_and_says_so(db, tmp_path):
    count, truncated = export_csv(db, SearchQuery(), tmp_path / "r.csv", max_rows=2)
    assert count == 2 and truncated and len(parse(tmp_path / "r.csv")) == 2


# -- the panel ---------------------------------------------------------------------------------
class Audit:
    def __init__(self):
        self.rows = []

    def log(self, action, user, **kw):
        self.rows.append((action, user, kw))


def panel(qtbot, db, role, audit=None):
    db.insert_user(f"u-{role}", f"{role}1", hash_password("pw"), role)
    p = SearchPanel(db, Session.login(f"{role}1", "pw", db), audit=audit)
    qtbot.addWidget(p)
    p.search()
    return p


def test_an_admin_downloads_and_it_is_audited(qtbot, db, tmp_path):
    audit = Audit()
    p = panel(qtbot, db, "admin", audit)
    assert p.export_btn.isEnabled()
    assert p.export_results(str(tmp_path / "x.csv")) == 3
    assert audit.rows[0][0] == "search_export" and audit.rows[0][2]["documents"] == 3
    assert "3 document(s) saved" in p.summary.text()


def test_the_download_covers_all_matches_not_just_the_loaded_page(qtbot, db, tmp_path, monkeypatch):
    import ui.search_panel as sp
    monkeypatch.setattr(sp, "PAGE", 1)
    p = panel(qtbot, db, "admin")
    assert p.table.rowCount() == 1                                   # one row on screen
    assert p.export_results(str(tmp_path / "x.csv")) == 3            # all three downloaded


def test_a_role_without_report_export_cannot_download(qtbot, db, tmp_path):
    p = panel(qtbot, db, "operator")
    assert not p.export_btn.isEnabled()
    assert p.export_results(str(tmp_path / "x.csv")) == 0 and not (tmp_path / "x.csv").exists()


def test_nothing_to_download_when_nothing_was_found(qtbot, db, tmp_path):
    p = panel(qtbot, db, "admin")
    p.text.setText("zzzz")
    p.search()
    assert not p.export_btn.isEnabled() and p.export_results(str(tmp_path / "x.csv")) == 0


def test_an_unwritable_place_gives_a_message_not_a_crash(qtbot, db, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a))
    p = panel(qtbot, db, "admin")
    (tmp_path / "blocker").write_text("a file, not a folder")
    assert p.export_results(str(tmp_path / "blocker" / "x.csv")) == 0
    assert len(warned) == 1
