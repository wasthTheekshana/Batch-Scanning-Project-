import json

import pytest

from core.central_state import (DEFAULT_POLL, MAX_POLL, MIN_POLL, CentralState,
                                clamp_poll)
from data.keystore import KeyStore, ProtectedBlob, protect_bytes, unprotect_bytes


def test_protect_roundtrip_and_unknown_format():
    assert unprotect_bytes(protect_bytes(b"s3cret", "test")) == b"s3cret"
    with pytest.raises(RuntimeError):
        unprotect_bytes(b"BOGUS\0abc")


def test_protected_blob_roundtrip_overwrite_delete(tmp_path):
    blob = ProtectedBlob(tmp_path / "keys" / "x.bin")
    assert blob.load() is None and not blob.exists()
    blob.save(b"one")
    assert blob.exists() and blob.load() == b"one"
    blob.save(b"two")
    assert blob.load() == b"two"
    blob.delete()
    assert blob.load() is None
    blob.delete()  # idempotent


def test_protected_blob_is_not_plaintext_on_disk_when_dpapi_available(tmp_path):
    blob = ProtectedBlob(tmp_path / "x.bin")
    blob.save(b"PLAINTEXT-MARKER-12345")
    raw = (tmp_path / "x.bin").read_bytes()
    if raw.startswith(b"DPAPI1\0"):
        assert b"PLAINTEXT-MARKER-12345" not in raw
    else:
        pytest.skip("pywin32/DPAPI not available in this environment")


def test_keystore_behaviour_unchanged(tmp_path):
    ks = KeyStore(tmp_path / "keys" / "master.bin")
    key = ks.get_or_create()
    assert len(key) == 32 and ks.get_or_create() == key


def test_clamp_poll():
    assert clamp_poll(5) == MIN_POLL and clamp_poll(10**9) == MAX_POLL
    assert clamp_poll(120) == 120 and clamp_poll("x") == DEFAULT_POLL
    assert clamp_poll(True) == DEFAULT_POLL


def test_fresh_state_is_unregistered(tmp_path):
    st = CentralState(tmp_path)
    assert not st.is_registered() and st.api_key() is None
    assert st.settings.poll_interval_seconds == DEFAULT_POLL


def test_register_result_persists_and_survives_reload(tmp_path):
    st = CentralState(tmp_path)
    st.register_result("https://c.example", "pc-1", "dsk_KEY", 90)
    st2 = CentralState(tmp_path)
    assert st2.is_registered() and st2.api_key() == "dsk_KEY"
    assert st2.settings.pc_id == "pc-1" and st2.settings.poll_interval_seconds == 90
    assert st2.settings.server_url == "https://c.example"


def test_api_key_never_written_to_central_json(tmp_path):
    st = CentralState(tmp_path)
    st.register_result("https://c.example", "pc-1", "dsk_SECRETKEY", 600)
    text = (tmp_path / "central.json").read_text(encoding="utf-8")
    assert "dsk_SECRETKEY" not in text and "api_key" not in text


def test_update_and_clear(tmp_path):
    st = CentralState(tmp_path)
    st.register_result("https://c.example", "pc-1", "k", 600)
    st.update(auth_failed=True, last_sync_status="auth failed (401)",
              poll_interval_seconds=5)
    assert st.settings.auth_failed and st.settings.poll_interval_seconds == MIN_POLL
    assert CentralState(tmp_path).settings.auth_failed is True
    st.clear()
    assert not st.is_registered() and not (tmp_path / "central.json").exists()
    assert CentralState(tmp_path).api_key() is None


def test_register_result_resets_auth_failed(tmp_path):
    st = CentralState(tmp_path)
    st.register_result("https://c.example", "pc-1", "old", 600)
    st.update(auth_failed=True)
    st.register_result("https://c.example", "pc-1", "new", 600)
    assert st.settings.auth_failed is False and st.api_key() == "new"


def test_corrupt_or_partial_json_falls_back_to_defaults(tmp_path):
    (tmp_path / "central.json").write_text("{not json", encoding="utf-8")
    assert CentralState(tmp_path).settings.server_url == ""
    (tmp_path / "central.json").write_text(
        json.dumps({"server_url": "https://x", "bogus_field": 1,
                    "poll_interval_seconds": "abc"}), encoding="utf-8")
    s = CentralState(tmp_path).settings
    assert s.server_url == "https://x" and s.poll_interval_seconds == DEFAULT_POLL


def test_registered_requires_the_key_file(tmp_path):
    st = CentralState(tmp_path)
    st.register_result("https://c.example", "pc-1", "k", 600)
    (tmp_path / "keys" / "central_api_key.bin").unlink()
    assert not CentralState(tmp_path).is_registered()


# ---- hardening (Task 5, R3) --------------------------------------------------
def test_api_key_swallows_any_exception_and_logs_only_the_type(tmp_path, caplog):
    state = CentralState(tmp_path)

    def boom():
        raise ValueError("SECRET-KEY-MATERIAL")
    state._key.load = boom
    with caplog.at_level("DEBUG"):
        assert state.api_key() is None
    assert "ValueError" in caplog.text
    assert "SECRET-KEY-MATERIAL" not in caplog.text


def test_update_with_unknown_field_changes_nothing(tmp_path):
    state = CentralState(tmp_path)
    with pytest.raises(AttributeError):
        state.update(last_sync_status="changed", bogus=1)
    assert state.settings.last_sync_status == ""
    assert not (tmp_path / "central.json").exists()


def test_update_whose_save_fails_leaves_memory_unchanged(tmp_path, monkeypatch):
    state = CentralState(tmp_path)

    def fail(self, settings=None):
        raise OSError("disk full")
    monkeypatch.setattr(CentralState, "_save", fail)
    with pytest.raises(OSError):
        state.update(auth_failed=True, last_sync_status="x")
    assert state.settings.auth_failed is False
    assert state.settings.last_sync_status == ""


def test_load_falls_back_to_defaults_for_wrongly_typed_fields(tmp_path):
    (tmp_path / "central.json").write_text(json.dumps({
        "server_url": 5, "pc_id": ["x"], "auth_failed": "no",
        "last_sync_at": 7, "last_sync_status": None,
        "poll_interval_seconds": 90}), encoding="utf-8")
    s = CentralState(tmp_path).settings
    assert s.server_url == "" and s.pc_id == "" and s.auth_failed is False
    assert s.last_sync_at == "" and s.last_sync_status == ""
    assert s.poll_interval_seconds == 90
