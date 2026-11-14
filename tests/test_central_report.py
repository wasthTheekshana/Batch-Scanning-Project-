"""What a PC reports to the central service: versions and counts, nothing else."""
import json
from datetime import datetime, timezone

import pytest

from sync.central_client import CentralAuthError, CentralClient, CentralUnavailable, PullResult
from sync.central_report import build_report
from sync.central_state import CentralState
from sync.central_sync import CentralSyncService
from projects.profile_manager import ProfileManager
from data.db import Database
from tests.test_central_sync import PID, FakeClient, project_entry
from version import __version__

NOW = datetime(2026, 10, 18, 9, 30, tzinfo=timezone.utc)


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "r.db")
    d.insert_project("p1", "Bank", {})
    yield d
    d.close()


def add_batch(db, batch_id, pages, created="2026-10-18T08:00:00+00:00"):
    db.insert_batch(batch_id, "p1", "kamal")
    db.update_batch_status(batch_id, "complete", pages)
    db.conn.execute("UPDATE batches SET created_at=? WHERE batch_id=?", (created, batch_id))
    db.conn.commit()


def add_doc(db, doc_id, batch="b1", created="2026-10-18T08:30:00+00:00", status="auto_approved",
            delivery=None, fields=None):
    db.insert_document(doc_id, batch, "p1", validation_status=status,
                       extracted_fields=fields or {"nic_number": "553216789V"},
                       delivery_detail=delivery or {})
    db.conn.execute("UPDATE documents SET created_at=? WHERE document_id=?", (created, doc_id))
    db.conn.commit()


# -- the counts ---------------------------------------------------------------------------------
def test_activity_counts_only_the_given_utc_day(db):
    add_batch(db, "b1", 12)
    add_batch(db, "b2", 5, created="2026-10-17T23:59:00+00:00")        # yesterday
    add_doc(db, "d1")
    add_doc(db, "d2")
    add_doc(db, "old", created="2026-10-17T10:00:00+00:00")
    assert db.activity_for_day("2026-10-18") == {"documents": 2, "pages": 12, "batches": 1, "errors": 0}
    assert db.activity_for_day("2026-10-17") == {"documents": 1, "pages": 5, "batches": 1, "errors": 0}
    assert db.activity_for_day("2026-10-01") == {"documents": 0, "pages": 0, "batches": 0, "errors": 0}


def test_errors_are_rejected_documents_and_failed_deliveries(db):
    add_batch(db, "b1", 3)
    add_doc(db, "ok", delivery={"local": {"success": True}})
    add_doc(db, "rejected", status="rejected")
    add_doc(db, "undelivered", delivery={"sftp": {"success": False}})
    assert db.activity_for_day("2026-10-18")["errors"] == 2


# -- the report -----------------------------------------------------------------------------------
def test_the_report_has_the_version_the_applied_projects_and_todays_counts(db):
    add_batch(db, "b1", 9)
    add_doc(db, "d1")
    db.set_central_project("proj-central", 4, managed=True)
    db.set_central_project("proj-local", 2, managed=False)             # not managed: not central's business
    report = build_report(db, NOW)
    assert report == {"app_version": __version__, "applied": {"proj-central": 4},
                      "stats": {"day": "2026-10-18", "documents": 1, "pages": 9, "batches": 1, "errors": 0},
                      "documents": {"waiting": 0, "refused": 0, "oldest_at": None}}


def test_the_report_says_how_many_documents_wait_and_since_when(db):
    db.outbox_gate = lambda: True
    db.insert_batch("bq", "p1", "kamal")
    db.insert_document("q1", "bq", "p1", extracted_fields={"nic": "1"})
    db.insert_document("q2", "bq", "p1", extracted_fields={"nic": "2"})
    db.outbox_fail("q2", "refused")
    docs = build_report(db, NOW)["documents"]
    assert (docs["waiting"], docs["refused"]) == (1, 1) and docs["oldest_at"]


def test_the_report_carries_nothing_about_a_documents_content(db):
    add_batch(db, "b1", 1)
    add_doc(db, "d1", fields={"nic_number": "553216789V", "applicant_name": "Kamal Perera",
                              "email": "kamal@example.com"})
    text = json.dumps(build_report(db, NOW))
    for secret in ("553216789V", "Kamal", "kamal@example.com", "d1", "p1", "b1"):
        assert secret not in text


# -- the client -------------------------------------------------------------------------------------
def client_with(status):
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append((method, url, headers, body))
        return status, b""
    return CentralClient("https://c.example", transport=transport), calls


def test_the_heartbeat_is_a_post_with_the_key_and_the_report_as_json():
    client, calls = client_with(204)
    client.heartbeat("pc-1", "dsk_KEY", {"app_version": "1.0.0"})
    method, url, headers, body = calls[0]
    assert (method, url) == ("POST", "https://c.example/pcs/pc-1/heartbeat")
    assert headers["Authorization"] == "Bearer dsk_KEY" and json.loads(body) == {"app_version": "1.0.0"}


@pytest.mark.parametrize("status, error", [(401, CentralAuthError), (403, CentralAuthError),
                                           (422, CentralUnavailable), (500, CentralUnavailable)])
def test_a_refused_heartbeat_is_reported_as_the_right_kind_of_error(status, error):
    client, _ = client_with(status)
    with pytest.raises(error):
        client.heartbeat("pc-1", "dsk_KEY", {})


# -- the sync sends it ----------------------------------------------------------------------------------
class Reporter(FakeClient):
    def __init__(self, *a, fail=None, **kw):
        super().__init__(*a, **kw)
        self.reports, self.fail = [], fail

    def heartbeat(self, pc_id, api_key, report):
        if self.fail:
            raise self.fail
        self.reports.append((pc_id, api_key, report))


@pytest.fixture()
def service(tmp_path):
    db = Database(tmp_path / "s.db")
    state = CentralState(tmp_path)
    holder = {"client": None}
    svc = CentralSyncService(db, ProfileManager(tmp_path / "projects", db), state, tmp_path,
                             client_factory=lambda url, timeout: holder["client"])
    yield type("S", (), dict(db=db, state=state, svc=svc, holder=holder))
    db.close()


def test_a_successful_sync_sends_the_report_with_the_versions_just_applied(service):
    service.state.register_result("https://c.example", "pc-1", "dsk_KEY", 600)
    service.holder["client"] = Reporter(pull=PullResult("pc-1", 600, [project_entry(version=3)]))
    assert service.svc.sync_once().status == "ok"
    (pc_id, key, report), = service.holder["client"].reports
    assert (pc_id, key) == ("pc-1", "dsk_KEY")
    assert report["applied"] == {PID: 3} and report["app_version"] == __version__


def test_a_report_that_fails_never_changes_the_syncs_result(service):
    service.state.register_result("https://c.example", "pc-1", "dsk_KEY", 600)
    for failure in (CentralUnavailable("down"), CentralAuthError(401), ValueError("odd"), AttributeError("old")):
        service.holder["client"] = Reporter(pull=PullResult("pc-1", 600, [project_entry(version=1)]), fail=failure)
        assert service.svc.sync_once().status == "ok"


def test_nothing_is_sent_when_the_pull_failed_or_the_pc_is_not_enrolled(service):
    service.holder["client"] = Reporter(pull=PullResult("pc-1", 600, []))
    assert service.svc.sync_once().status == "not_registered"
    assert service.holder["client"].reports == []
    service.state.register_result("https://c.example", "pc-1", "dsk_KEY", 600)
    service.holder["client"] = Reporter(exc=CentralUnavailable("down"))
    assert service.svc.sync_once().status == "unavailable"
    assert service.holder["client"].reports == []


def test_an_old_client_without_a_heartbeat_method_still_syncs(service):
    service.state.register_result("https://c.example", "pc-1", "dsk_KEY", 600)
    service.holder["client"] = FakeClient(pull=PullResult("pc-1", 600, [project_entry()]))   # no heartbeat()
    assert service.svc.sync_once().status == "ok"
