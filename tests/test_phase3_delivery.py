"""Phase 3 delivery tests: real SFTP (mocked paramiko), CD/DVD burn
(fake burner CLI), multi-channel pipeline delivery."""
import json
import sys

import pytest

import delivery.sftp_channel as sftp_mod
from delivery.base import BaseDeliveryChannel, DeliveryResult
from delivery.burn_queue import BurnQueue
from delivery.cddvd_channel import CDDVDChannel
from delivery.sftp_channel import SFTPChannel


# ---- fake paramiko ---------------------------------------------------------------

class FakeSFTP:
    """In-memory SFTP server: remote paths -> bytes. `state` is shared
    across reconnects so transient-failure counters persist."""
    def __init__(self, store, state, corrupt=False):
        self.store = store
        self.state = state
        self.corrupt = corrupt
        self.dirs = set()

    def stat(self, path):
        if path in self.dirs or path in self.store:
            class S:
                st_size = len(self.store.get(path, b""))
            s = S()
            s.st_size = len(self.store.get(path, b""))
            return s
        raise FileNotFoundError(path)

    def mkdir(self, path):
        self.dirs.add(path)

    def put(self, local, remote):
        if self.state.get("fail_uploads", 0) > 0:
            self.state["fail_uploads"] -= 1
            raise IOError("network glitch")
        data = open(local, "rb").read()
        self.store[remote] = data[:-1] if self.corrupt else data

    def close(self):
        pass


class FakeParamiko:
    def __init__(self, store, fail_uploads=0, corrupt=False,
                 connect_error=False):
        self._store = store
        self._state = {"fail_uploads": fail_uploads}
        self._corrupt = corrupt
        self._connect_error = connect_error

    class _Transport:
        def __init__(self, addr):
            self.addr = addr

        def connect(self, **kw):
            pass

        def close(self):
            pass

    def Transport(self, addr):
        if self._connect_error:
            raise ConnectionError("host unreachable")
        return self._Transport(addr)

    @property
    def SFTPClient(self):
        outer = self

        class _Factory:
            @staticmethod
            def from_transport(transport):
                return FakeSFTP(outer._store, outer._state, outer._corrupt)
        return _Factory


SFTP_CFG = {"enabled": True, "host": "192.168.1.10", "username": "scans",
            "password": "pw", "remote_path": "incoming",
            "retry_delay": 0.01}


@pytest.fixture()
def doc(tmp_path):
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-content-here")
    return p


def test_sftp_upload_success(monkeypatch, doc):
    store = {}
    monkeypatch.setattr(sftp_mod, "_paramiko",
                        lambda: FakeParamiko(store))
    ch = SFTPChannel(SFTP_CFG)
    assert ch.is_available()
    result = ch.deliver(str(doc), {})
    assert result.success
    assert result.destination == "incoming/doc.pdf"
    assert store["incoming/doc.pdf"] == doc.read_bytes()
    assert result.checksum  # md5 recorded


def test_sftp_respects_output_subfolder(monkeypatch, doc):
    store = {}
    monkeypatch.setattr(sftp_mod, "_paramiko", lambda: FakeParamiko(store))
    result = SFTPChannel(SFTP_CFG).deliver(
        str(doc), {"output_folder": "ClientA/Invoices"})
    assert result.success
    assert result.destination == "incoming/ClientA/Invoices/doc.pdf"


def test_sftp_retries_transient_failure(monkeypatch, doc):
    fake = FakeParamiko({}, fail_uploads=2)  # single instance: the failure
    monkeypatch.setattr(sftp_mod, "_paramiko", lambda: fake)  # counter must
    result = SFTPChannel(SFTP_CFG).deliver(str(doc), {})      # span retries
    assert result.success  # third attempt succeeded


def test_sftp_size_mismatch_fails(monkeypatch, doc):
    store = {}
    monkeypatch.setattr(sftp_mod, "_paramiko",
                        lambda: FakeParamiko(store, corrupt=True))
    result = SFTPChannel(SFTP_CFG).deliver(str(doc), {})
    assert not result.success
    assert "size mismatch" in result.detail["error"]


def test_sftp_connect_error_exhausts_retries(monkeypatch, doc):
    monkeypatch.setattr(sftp_mod, "_paramiko",
                        lambda: FakeParamiko({}, connect_error=True))
    result = SFTPChannel(SFTP_CFG).deliver(str(doc), {})
    assert not result.success and result.detail["attempts"] == 3


def test_sftp_unavailable_without_host():
    assert not SFTPChannel({"enabled": True, "username": "x"}).is_available()


# ---- burn queue -------------------------------------------------------------------

def test_burn_queue_add_and_manifest(tmp_path, doc):
    q = BurnQueue(tmp_path / "queue")
    q.add(doc)
    q.add(doc)  # same name — must not overwrite
    manifest = q.manifest()
    assert q.file_count() == 2
    assert manifest["files"][0]["md5"] == manifest["files"][1]["md5"]
    assert {e["file"] for e in manifest["files"]} == {"doc.pdf", "doc_1.pdf"}


def test_burn_queue_verify(tmp_path, doc):
    q = BurnQueue(tmp_path / "queue")
    q.add(doc)
    disc = tmp_path / "disc"
    disc.mkdir()
    (disc / "doc.pdf").write_bytes(doc.read_bytes())
    assert q.verify_against(disc) == []
    (disc / "doc.pdf").write_bytes(b"corrupted")
    assert q.verify_against(disc) == ["doc.pdf"]


# ---- CD/DVD channel with fake burner CLI ---------------------------------------------

FAKE_BURNER = """
import shutil, sys, pathlib
src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
dst.mkdir(parents=True, exist_ok=True)
for f in src.iterdir():
    if f.is_file() and f.name != "manifest.json":
        shutil.copy2(f, dst / f.name)
"""


def make_channel(tmp_path, burner_extra=""):
    script = tmp_path / "fake_burner.py"
    script.write_text(FAKE_BURNER + burner_extra)
    disc = tmp_path / "disc"
    cfg = {"enabled": True,
           "staging_dir": str(tmp_path / "burn_queue"),
           "verify_dir": str(disc),
           "burner_cmd": f'"{sys.executable}" "{script}" '
                         f'"{{source_dir}}" "{disc}"'}
    return CDDVDChannel(cfg), disc


def test_cddvd_stage_then_burn_verified(tmp_path, doc):
    ch, disc = make_channel(tmp_path)
    staged = ch.deliver(str(doc), {"document_id": "d1"})
    assert not staged.success and staged.detail["queued"]
    assert ch.queue.file_count() == 1

    result = ch.burn(label="TEST_DISC")
    assert result.success, result.detail
    assert result.detail["verified"] is True
    assert (disc / "doc.pdf").read_bytes() == doc.read_bytes()
    assert ch.queue.file_count() == 0  # cleared after good burn

    burn_log = json.loads(
        (tmp_path / "burn_log.json").read_text(encoding="utf-8"))
    assert burn_log[0]["label"] == "TEST_DISC"


def test_cddvd_burn_verify_failure_keeps_queue(tmp_path, doc):
    corrupt = "\n(dst / 'doc.pdf').write_bytes(b'corrupted')\n"
    ch, _disc = make_channel(tmp_path, burner_extra=corrupt)
    ch.deliver(str(doc), {})
    result = ch.burn()
    assert not result.success
    assert result.detail["verify_failures"] == ["doc.pdf"]
    assert ch.queue.file_count() == 1  # kept for re-burn


def test_cddvd_capacity_guard(tmp_path, doc):
    ch, _ = make_channel(tmp_path)
    ch.config["disc_capacity_mb"] = 0.00001
    result = ch.deliver(str(doc), {})
    assert not result.success
    assert "capacity" in result.detail["error"]


def test_cddvd_empty_queue_burn_rejected(tmp_path):
    ch, _ = make_channel(tmp_path)
    result = ch.burn()
    assert not result.success and "empty" in result.detail["error"]


# ---- multi-channel pipeline delivery ---------------------------------------------------

class RecordingChannel(BaseDeliveryChannel):
    def __init__(self, name, status, fail_times=0):
        self._name = name
        self.status_value = status
        self.fail_times = fail_times
        self.calls = 0

    @property
    def name(self):
        return self._name

    def is_available(self):
        return True

    def deliver(self, doc_path, metadata):
        self.calls += 1
        if self.calls <= self.fail_times:
            return DeliveryResult(False, self._name,
                                  detail={"error": "boom"})
        return DeliveryResult(True, self._name, destination=doc_path)


def test_pipeline_multi_channel_and_trigger(tmp_path):
    import cv2
    from core.job_queue import Pipeline
    from core.models import ScanJob
    from data.db import Database
    from tests.conftest import make_text_page
    from tests.test_pipeline import make_profile, wait_for

    db = Database(tmp_path / "mc.db")
    profile = make_profile(tmp_path)
    profile["rules"] = {"rule_set_id": "rs", "version": 1, "rules": [{
        "id": "r1", "name": "also push to sftp",
        "condition": {"field": "qc_score", "op": "gte", "value": 0},
        "actions": [{"type": "trigger_delivery", "value": "fake_sftp"}],
    }]}
    local = RecordingChannel("local_folder", "delivered_local")
    flaky = RecordingChannel("fake_sftp", "delivered_sftp", fail_times=2)
    from export.batch_exporter import export_document
    pipeline = Pipeline(db, profile, [local], export_document,
                        working_dir=tmp_path / "staging",
                        trigger_pool={"fake_sftp": flaky})
    pipeline.start()
    try:
        p = tmp_path / "p1.png"
        cv2.imwrite(str(p), make_text_page())
        pipeline.submit_batch("B1", [ScanJob(
            batch_id="B1", project_id=profile["project_id"],
            page_no=1, image_path=str(p))])
        assert wait_for(lambda: local.calls >= 1 and flaky.calls >= 3)
        assert wait_for(lambda: any(
            d["delivery_status"] != "pending"
            for d in db.list_documents("B1")))
        doc = db.list_documents("B1")[0]
        detail = json.loads(doc["delivery_detail"])
        assert detail["local_folder"]["success"]
        assert detail["fake_sftp"]["success"]  # succeeded on 3rd retry
        assert doc["delivery_status"] == "delivered_local"
    finally:
        pipeline.shutdown()
        db.close()
