"""Finding documents by their index values, project, status and date."""
import pytest

from search.search import (MAX_LIMIT, SearchQuery, count_documents,
                         known_field_names, search_documents)
from data.db import Database


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "s.db")
    d.insert_project("p1", "Bank - Customer file", {})
    d.insert_project("p2", "Invoices", {})
    d.insert_batch("b1", "p1", "alice")
    d.insert_batch("b2", "p1", "bob")
    d.insert_batch("b3", "p2", "alice")

    def doc(doc_id, batch, project, created, status="auto_approved",
            delivery="delivered_local", fields=None):
        d.insert_document(doc_id, batch, project, page_range="1-3", qc_score=90,
                          validation_status=status, extracted_fields=fields or {})
        d.update_document(doc_id, delivery_status=delivery,
                          storage_path=f"/out/{doc_id}.pdf")
        d.conn.execute("update documents set created_at=? where document_id=?",
                       (created, doc_id))
        d.conn.commit()

    doc("d1", "b1", "p1", "2026-10-10T09:00:00+00:00",
        fields={"barcode": "CIF-100234", "nic_number": "199012345678",
                "applicant_name": "Nimal Perera", "doc_types": "NIC, Application"})
    doc("d2", "b2", "p1", "2026-10-12T09:00:00+00:00", status="pending_qc",
        delivery="pending",
        fields={"barcode": "CIF-100235", "nic_number": "856789012V",
                "applicant_name": "Kamala Silva"})
    doc("d3", "b3", "p2", "2026-10-14T23:30:00+00:00",
        fields={"invoice_no": "INV-777", "vendor_name": "Perera & Sons"})
    yield d
    d.close()


def ids(rows):
    return [r["document_id"] for r in rows]


def find(db, **kw):
    return ids(search_documents(db, SearchQuery(**kw)))


# -- free text -------------------------------------------------------------------------------
def test_a_value_finds_its_document(db):
    assert find(db, text="199012345678") == ["d1"]
    assert find(db, text="CIF-100235") == ["d2"]


def test_search_is_case_insensitive_and_matches_part_of_a_value(db):
    assert find(db, text="nimal") == ["d1"]
    assert find(db, text="100234") == ["d1"]


def test_every_word_must_match_but_not_in_the_same_field(db):
    assert find(db, text="perera 199012") == ["d1"]
    assert find(db, text="perera inv-777") == ["d3"]          # vendor name + invoice no
    assert find(db, text="nimal silva") == []


def test_field_names_are_not_searched_only_values(db):
    assert find(db, text="nic_number") == []
    assert find(db, text="applicant") == []


def test_percent_and_underscore_are_ordinary_characters(db):
    assert find(db, text="%") == []
    assert find(db, text="CIF_100234") == []                  # underscore is not a wildcard


def test_the_document_id_is_searchable_too(db):
    assert find(db, text="d2") == ["d2"]


def test_empty_text_lists_everything_newest_first(db):
    assert find(db) == ["d3", "d2", "d1"]


# -- filters -----------------------------------------------------------------------------------
def test_a_field_filter_matches_that_field_only(db):
    assert find(db, field_filters={"nic_number": "8567"}) == ["d2"]
    assert find(db, field_filters={"applicant_name": "silva"}) == ["d2"]
    assert find(db, field_filters={"vendor_name": "199012"}) == []   # other field


def test_field_filters_combine_with_and(db):
    assert find(db, field_filters={"nic_number": "1990", "applicant_name": "nimal"}) == ["d1"]
    assert find(db, field_filters={"nic_number": "1990", "applicant_name": "silva"}) == []


def test_a_field_name_with_quotes_cannot_break_the_query(db):
    assert find(db, field_filters={'a"b': "x"}) == []
    assert find(db, field_filters={"x') OR 1=1 --": "x"}) == []


def test_project_and_status_filters(db):
    assert find(db, project_id="p2") == ["d3"]
    assert find(db, validation_status="pending_qc") == ["d2"]
    assert find(db, delivery_status="pending") == ["d2"]
    assert find(db, project_id="p1", validation_status="auto_approved") == ["d1"]


def test_date_filters_are_inclusive_by_day(db):
    assert find(db, date_from="2026-10-12") == ["d3", "d2"]
    assert find(db, date_to="2026-10-12") == ["d2", "d1"]
    assert find(db, date_from="2026-10-14", date_to="2026-10-14") == ["d3"]  # 23:30 counts
    assert find(db, date_from="2026-10-15") == []


def test_a_user_who_may_only_see_their_own_batches(db):
    assert find(db, only_operator="alice") == ["d3", "d1"]
    assert find(db, only_operator="bob") == ["d2"]
    assert find(db, only_operator="nobody") == []
    assert find(db, only_operator="alice", text="perera") == ["d3", "d1"]


# -- results -------------------------------------------------------------------------------------
def test_a_result_carries_what_the_screen_needs(db):
    row = search_documents(db, SearchQuery(text="100234"))[0]
    assert row["project_name"] == "Bank - Customer file" and row["operator"] == "alice"
    assert row["fields"]["nic_number"] == "199012345678"
    assert row["storage_path"] == "/out/d1.pdf" and row["page_range"] == "1-3"
    assert row["validation_status"] == "auto_approved" and row["created_at"].startswith("2026-10-10")


def test_a_document_with_unreadable_fields_is_listed_and_never_breaks_the_search(db):
    db.conn.execute("update documents set extracted_fields='{not json' where document_id='d3'")
    db.conn.commit()
    assert "d3" in find(db)
    assert find(db, text="perera") == ["d1"]
    assert find(db, field_filters={"vendor_name": "sons"}) == []
    assert search_documents(db, SearchQuery(project_id="p2"))[0]["fields"] == {}


def test_paging_and_the_total_count(db):
    q = SearchQuery(limit=2)
    assert ids(search_documents(db, q)) == ["d3", "d2"]
    assert ids(search_documents(db, SearchQuery(limit=2, offset=2))) == ["d1"]
    assert count_documents(db, SearchQuery(limit=2)) == 3
    assert count_documents(db, SearchQuery(text="perera")) == 2


def test_the_limit_is_capped(db):
    q = SearchQuery(limit=10_000_000)
    assert q.effective_limit == MAX_LIMIT
    assert SearchQuery(limit=0).effective_limit == 1


def test_known_field_names_feed_the_filter_choices(db):
    names = known_field_names(db)
    assert {"nic_number", "applicant_name", "invoice_no", "vendor_name"} <= set(names)
    assert "doc_types" not in names and "barcode" not in names      # shown elsewhere
    assert known_field_names(db, project_id="p2") == ["invoice_no", "vendor_name"]


def test_the_database_query_helper_refuses_anything_but_a_select(db):
    with pytest.raises(ValueError, match="SELECT"):
        db.query("DELETE FROM documents")
    with pytest.raises(ValueError):
        db.query("  update documents set qc_score=0")
    assert db.query("select count(*) as n from documents")[0]["n"] == 3
