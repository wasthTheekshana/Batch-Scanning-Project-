"""Reporting finished documents to central: queued with the change, sent in order, retried after an outage, never
twice, and the PDF's bytes never leave the PC."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from sync.central_client import CentralAuthError, CentralRejected, CentralUnavailable
from sync.central_documents import BACKOFF_START, DocumentPusher, build_payload
from data.db import Database

T0 = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)
FIELDS = {"nic": "553216789V", "full_name": "Kamal Perera", "branch": "Galle"}


class State:
    def __init__(self, registered=True, auth_failed=False):
        self.registered = registered
        self.settings = SimpleNamespace(server_url="https://central.test", pc_id="pc-1", auth_failed=auth_failed)

    def is_registered(self):
        return self.registered

    def api_key(self):
        return "the-key"


class Client:
    """Stands in for the server: records what arrives, fails on demand."""

    def __init__(self):
        self.calls, self.error, self.fail_on = [], None, None

    def put_document(self, pc_id, api_key, document_id, part, body):
        assert (pc_id, api_key) == ("pc-1", "the-key")
        if self.error is not None and self.fail_on in (None, part):
            raise self.error
        self.calls.append((document_id, part, body))


@pytest.fixture()
def clock():
    return SimpleNamespace(now=T0)


@pytest.fixture()
def world(tmp_path, clock):
    db = Database(tmp_path / "pc.db")
    state, client = State(), Client()
    db.outbox_gate = state.is_registered
    db.insert_project("p1", "Bank", {})
    db.insert_batch("b1", "p1", "kamal")
    pusher = DocumentPusher(db, state, client_factory=lambda url, t: client, now=lambda: clock.now)
    yield SimpleNamespace(db=db, state=state, client=client, pusher=pusher, tmp=tmp_path, clock=clock)
    db.close()


def add_doc(world, doc_id="d1", pdf=b"%PDF pdf one", **over):
    path = world.tmp / f"{doc_id}.pdf"
    path.write_bytes(pdf)
    world.db.insert_document(doc_id, "b1", "p1", page_range="1-3", extracted_fields=FIELDS,
                             validation_status="approved", storage_path=str(path), **over)
    return path


def stats(world):
    return world.db.outbox_stats()


# -- queued together with the change -----------------------------------------------------------------------------
def test_nothing_is_queued_while_the_pc_is_not_enrolled(world):
    world.state.registered = False
    add_doc(world)
    assert stats(world)["waiting"] == 0


def test_a_document_and_its_later_changes_are_one_queue_entry(world):
    add_doc(world)
    world.db.update_document("d1", extracted_fields={**FIELDS, "full_name": "Kamal A Perera"})
    world.db.update_document("d1", delivery_status="delivered_local")
    assert stats(world)["waiting"] == 1
    row = world.db.conn.execute("select seq from sync_outbox").fetchone()
    assert row["seq"] == 3


def test_changes_that_central_does_not_need_are_not_queued(world):
    add_doc(world)
    world.pusher.push_once()
    assert stats(world)["waiting"] == 0
    world.db.update_document("d1", audit_trail=[{"a": 1}], qc_detail={"pages": {}})
    assert stats(world)["waiting"] == 0


def test_a_document_that_fails_to_save_leaves_no_queue_entry(world):
    with pytest.raises(Exception):
        world.db.insert_document("d9", "no-such-batch", "p1", extracted_fields=FIELDS)
    assert world.db.conn.execute("select count(*) from sync_outbox").fetchone()[0] == 0


def test_existing_documents_can_be_queued_when_a_pc_first_connects(world):
    world.state.registered = False
    add_doc(world, "a")
    add_doc(world, "b")
    assert stats(world)["waiting"] == 0
    assert world.db.outbox_enqueue_existing() == 2
    assert stats(world)["waiting"] == 2


# -- what is sent -------------------------------------------------------------------------------------------------------
def test_details_go_before_the_fields_and_describe_the_pdf_without_sending_it(world):
    path = add_doc(world)
    result = world.pusher.push_once()
    assert (result.sent, result.stopped) == (1, None)
    (doc, part1, meta), (_, part2, index) = world.client.calls
    assert (doc, part1, part2) == ("d1", "meta", "index")
    assert meta["sha256"] == hashlib.sha256(b"%PDF pdf one").hexdigest() and meta["size"] == path.stat().st_size
    assert meta["file_name"] == "d1.pdf" and meta["page_count"] == 3 and meta["project_id"] == "p1"
    assert index["fields"] == FIELDS and index["validation_status"] == "approved"
    assert b"%PDF" not in json.dumps([meta, index]).encode()
    assert stats(world)["waiting"] == 0 and stats(world)["last_sent_at"]


def test_the_payload_copes_with_odd_field_values_and_a_missing_pdf(world):
    world.db.insert_document("d2", "b1", "p1", extracted_fields={"a b": ["x", "y"], "n": 5, "gone": None,
                                                                 "nested": {"k": 1}}, storage_path="C:/nowhere.pdf")
    meta, index = build_payload(world.db, "d2")
    assert index["fields"] == {"a_b": "x, y", "n": "5"}
    assert "sha256" not in meta and "file_name" not in meta


def test_what_was_delivered_is_reported_by_channel_name(world):
    add_doc(world, delivery_status="delivered_local",
            delivery_detail={"local_folder": {"success": True, "checksum": "x"}, "sftp": {"success": False}})
    meta, _ = build_payload(world.db, "d1")
    assert meta["delivered_to"] == ["local_folder", "kept on the scanning PC"]


# -- outages ---------------------------------------------------------------------------------------------------------------
def test_an_outage_keeps_the_document_and_it_arrives_exactly_once_afterwards(world):
    add_doc(world)
    world.client.error = CentralUnavailable("down")
    result = world.pusher.push_once()
    assert (result.sent, result.stopped) == (0, "unavailable") and world.client.calls == []
    assert stats(world)["waiting"] == 1 and stats(world)["last_error"]
    assert world.pusher.push_once().sent == 0                    # still inside the wait: not hammered
    world.client.error = None
    world.clock.now = T0 + timedelta(seconds=BACKOFF_START * 2)
    assert world.pusher.push_once().sent == 1
    assert world.pusher.push_once().sent == 0                    # nothing more to say
    assert [(d, p) for d, p, _ in world.client.calls] == [("d1", "meta"), ("d1", "index")]
    assert stats(world)["waiting"] == 0 and stats(world)["last_error"] is None


def test_the_wait_grows_with_each_failed_try(world):
    add_doc(world)
    world.client.error = CentralUnavailable("down")
    waits = []
    for _ in range(3):
        world.pusher.push_once()
        row = world.db.conn.execute("select attempts, next_try_at from sync_outbox").fetchone()
        waits.append((datetime.fromisoformat(row["next_try_at"]) - world.clock.now).total_seconds())
        world.clock.now += timedelta(hours=2)
    assert waits[0] < waits[1] < waits[2] <= 3600 * 1.2


def test_a_failure_after_the_details_went_through_resends_both_next_time(world):
    add_doc(world)
    world.client.error, world.client.fail_on = CentralUnavailable("down"), "index"
    world.pusher.push_once()
    assert [p for _, p, _ in world.client.calls] == ["meta"]
    world.client.error = None
    world.clock.now += timedelta(hours=1)
    world.pusher.push_once()
    assert [p for _, p, _ in world.client.calls] == ["meta", "meta", "index"]      # the server treats the repeat as a no-op


def test_a_change_made_while_sending_is_sent_again(world):
    add_doc(world)

    class Racing(Client):
        def put_document(self, *a):
            super().put_document(*a)
            if a[3] == "index" and len(self.calls) == 2:
                world.db.update_document("d1", validation_status="rejected")        # the operator acts meanwhile

    racing = Racing()
    world.pusher._factory = lambda url, t: racing
    world.pusher.push_once()
    assert stats(world)["waiting"] == 1
    world.pusher.push_once()
    assert racing.calls[-1][2]["validation_status"] == "rejected"
    assert stats(world)["waiting"] == 0


# -- refusals --------------------------------------------------------------------------------------------------------------
def test_a_document_the_server_refuses_is_set_aside_and_can_be_retried(world):
    add_doc(world, "bad")
    add_doc(world, "good")
    world.client.error = CentralRejected("no", 422)
    world.client.fail_on = "meta"
    first = world.pusher.push_once()
    assert first.refused == 2 and stats(world)["failed"] == 2 and stats(world)["waiting"] == 0
    world.client.error = None
    assert world.pusher.push_once().sent == 0                    # set aside: not retried by itself
    assert world.db.outbox_retry_failed() == 2
    assert world.pusher.push_once().sent == 2


def test_a_changed_document_is_tried_again_even_after_a_refusal(world):
    add_doc(world)
    world.client.error, world.client.fail_on = CentralRejected("no", 422), "meta"
    world.pusher.push_once()
    world.client.error = None
    world.db.update_document("d1", extracted_fields={**FIELDS, "branch": "Kandy"})
    assert world.pusher.push_once().sent == 1


def test_a_refused_key_stops_everything_and_marks_nothing(world):
    add_doc(world)
    world.client.error = CentralAuthError(401)
    result = world.pusher.push_once()
    assert result.stopped == "access_refused" and stats(world)["waiting"] == 1 and stats(world)["failed"] == 0


def test_nothing_is_sent_when_not_connected_or_after_the_key_failed(world):
    add_doc(world)
    world.state.registered = False
    assert world.pusher.push_once().stopped == "not_connected"
    world.state.registered, world.state.settings.auth_failed = True, True
    assert world.pusher.push_once().stopped == "access_refused"
    assert world.client.calls == []


def test_a_document_deleted_locally_is_dropped_from_the_queue(world):
    add_doc(world)
    world.db.conn.execute("delete from documents where document_id='d1'")
    assert world.pusher.push_once().sent == 0
    assert world.db.conn.execute("select count(*) from sync_outbox").fetchone()[0] == 0


def test_an_unexpected_error_in_one_document_does_not_stop_the_others(world):
    add_doc(world, "a")
    add_doc(world, "b")

    class Odd(Client):
        def put_document(self, pc, key, doc, part, body):
            if doc == "a":
                raise RuntimeError("boom with a NIC 553216789V in it")
            super().put_document(pc, key, doc, part, body)

    odd = Odd()
    world.pusher._factory = lambda url, t: odd
    result = world.pusher.push_once()
    assert result.sent == 1 and result.retry_later == 1
    assert "553216789V" not in (stats(world)["last_error"] or "")             # the error text keeps only its type


# -- what the Admin > Central tab says ---------------------------------------------------------------------------
def test_the_tab_describes_the_queue_in_plain_words():
    from ui.central_tab import documents_text
    now = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
    quiet = {"waiting": 0, "failed": 0, "oldest": None, "last_sent_at": None, "last_error": None}
    assert documents_text(quiet, now) == "No documents have been reported to central yet."
    assert documents_text({**quiet, "last_sent_at": "2026-10-18T11:59:00+00:00"}, now) == "All documents have been reported to central."
    busy = {"waiting": 3, "failed": 1, "oldest": "2026-10-18T09:00:00+00:00", "last_error": "HTTP 503",
            "last_sent_at": "2026-10-18T08:00:00+00:00"}
    text = documents_text(busy, now)
    assert "3 document(s) waiting" in text and "3 hours" in text and "HTTP 503" in text
    assert "1 document(s) were refused" in text and "4 hours ago" in text
