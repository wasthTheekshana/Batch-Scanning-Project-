import copy
import json
import logging
import os
import sqlite3
import threading
import time

import pytest

from core.central_client import (CentralAuthError, CentralRegistrationError,
                                 CentralUnavailable, PullResult, RegisterResult)
from core.central_state import CentralState
from core.central_sync import (CentralSyncService, SyncResult,
                               choose_pipeline_profile)
from core.config_overlay import default_local_sections
from core.profile_manager import DEFAULT_PROJECT, ProfileManager
from data.audit import AuditLogger
from data.db import Database

PID = "11111111-2222-3333-4444-555555555555"
PID2 = "99999999-2222-3333-4444-555555555555"


def project_entry(pid=PID, name="Invoices", version=1, lang="eng", **cfg_over):
    cfg = copy.deepcopy(DEFAULT_PROJECT)
    cfg.update(project_id=pid, name=name, **cfg_over)
    cfg["ocr"]["lang"] = lang
    cfg["rules"]["rule_set_id"] = "rs"
    return {"project_id": pid, "name": name, "version": version, "config": cfg}


class FakeClient:
    def __init__(self, pull=None, exc=None, register=None):
        self.pull, self.exc, self._register = pull, exc, register
        self.pulls = []
        self.registered = []

    def pull_config(self, pc_id, api_key, operator_id=""):
        self.pulls.append((pc_id, api_key, operator_id))
        if self.exc:
            raise self.exc
        return self.pull

    def register(self, code):
        self.registered.append(code)
        if isinstance(self._register, Exception):
            raise self._register
        return self._register


@pytest.fixture()
def env(tmp_path):
    db = Database(tmp_path / "c.db")
    pm = ProfileManager(tmp_path / "projects", db)
    state = CentralState(tmp_path)
    audit = AuditLogger(db, tmp_path / "logs")
    holder = {"client": FakeClient(pull=PullResult("pc-1", 600, []))}
    created = []

    def factory(url, timeout):
        created.append((url, timeout))
        return holder["client"]

    svc = CentralSyncService(db, pm, state, tmp_path, audit=audit,
                             client_factory=factory,
                             operator_fn=lambda: "alice")
    yield type("Env", (), dict(db=db, pm=pm, state=state, svc=svc, audit=audit,
                               holder=holder, created=created, root=tmp_path))
    audit.close()
    db.close()


def register(env):
    env.state.register_result("https://c.example", "pc-1", "dsk_KEY", 600)


def pull(env, *entries, poll=600):
    env.holder["client"] = FakeClient(pull=PullResult("pc-1", poll, list(entries)))


def actions(env):
    return [r["action"] for r in env.db.conn.execute(
        "SELECT action FROM audit_log ORDER BY log_id")]


# ---- not registered ---------------------------------------------------------------
def test_unregistered_pc_makes_no_network_call(env):
    r = env.svc.sync_once()
    assert r.status == "not_registered" and not r.applied
    assert env.created == [] and env.holder["client"].pulls == []


# ---- first pull / updates ------------------------------------------------------------
def test_first_pull_creates_project_with_seeded_local_sections(env):
    register(env)
    pull(env, project_entry(lang="deu"))
    r = env.svc.sync_once()
    assert r.status == "ok" and r.applied == [PID] and r.restart_needed
    cfg = env.pm.load(PID)
    assert cfg["ocr"]["lang"] == "deu" and cfg["version"] == 1
    assert cfg["export"]["output_path"] == str(env.root / "output")
    rec = env.db.get_central_project(PID)
    assert rec["central_version"] == 1 and rec["managed"] == 1
    assert env.holder["client"].pulls == [("pc-1", "dsk_KEY", "alice")]
    assert env.created == [("https://c.example", 15.0)]
    assert "central_project_applied" in actions(env)


def test_same_version_again_is_a_noop(env):
    register(env)
    pull(env, project_entry())
    env.svc.sync_once()
    pull(env, project_entry())
    r = env.svc.sync_once()
    assert r.applied == [] and r.unchanged == 1 and not r.restart_needed
    assert env.pm.load(PID)["version"] == 1          # no spurious local version bump


def test_new_central_version_updates_and_preserves_local_sections(env):
    register(env)
    pull(env, project_entry(lang="eng"))
    env.svc.sync_once()
    local = env.pm.load(PID)
    local["export"]["output_path"] = "D:/my/output"
    local["delivery"]["sftp"] = {"enabled": True, "host": "10.1.1.1",
                                 "username": "u", "password": "pw"}
    env.pm.save(local)                               # local edits to local-only sections
    pull(env, project_entry(version=2, lang="fra"))
    r = env.svc.sync_once()
    assert r.applied == [PID]
    cfg = env.pm.load(PID)
    assert cfg["ocr"]["lang"] == "fra"
    assert cfg["export"]["output_path"] == "D:/my/output"
    assert cfg["delivery"]["sftp"]["password"] == "pw"
    assert env.db.get_central_project(PID)["central_version"] == 2


def test_adopts_existing_local_project_with_same_id_keeping_local_sections(env):
    register(env)
    local = copy.deepcopy(DEFAULT_PROJECT)
    local.update(project_id=PID, name="Old", version=4,
                 created="2026-01-01T00:00:00+00:00")
    local["rules"]["rule_set_id"] = "r"
    local["export"]["output_path"] = "E:/keep"
    env.pm.adopt(local)
    pull(env, project_entry(name="Invoices", version=1))
    env.svc.sync_once()
    cfg = env.pm.load(PID)
    assert cfg["name"] == "Invoices" and cfg["export"]["output_path"] == "E:/keep"


def test_poll_interval_and_status_recorded(env):
    register(env)
    pull(env, poll=90)
    r = env.svc.sync_once()
    s = env.state.settings
    assert r.status == "ok" and s.poll_interval_seconds == 90
    assert s.last_sync_status == "ok"
    assert s.last_sync_at.endswith("Z")


# ---- unassignment -----------------------------------------------------------------------
def test_unassigned_project_is_kept_but_unmanaged(env):
    register(env)
    pull(env, project_entry(), project_entry(pid=PID2, name="Other"))
    env.svc.sync_once()
    pull(env, project_entry(version=1, pid=PID2, name="Other"))  # PID disappears
    r = env.svc.sync_once()
    assert r.unassigned == [PID]
    assert env.db.get_project(PID) is not None and env.pm.load(PID)
    assert env.db.get_central_project(PID)["managed"] == 0
    assert env.db.get_central_project(PID2)["managed"] == 1
    assert "central_project_unassigned" in actions(env)


def test_reassigned_project_becomes_managed_again(env):
    register(env)
    pull(env, project_entry())
    env.svc.sync_once()
    pull(env)
    env.svc.sync_once()
    pull(env, project_entry(version=1))
    r = env.svc.sync_once()
    assert r.applied == [PID] and env.db.get_central_project(PID)["managed"] == 1


# ---- bad entries ----------------------------------------------------------------------------
def test_path_traversal_id_is_skipped_nothing_written_outside(env):
    register(env)
    evil = project_entry(pid="../../evil", name="Evil")
    pull(env, evil, project_entry(pid=PID2, name="Good"))
    r = env.svc.sync_once()
    assert r.status == "ok" and r.applied == [PID2]
    assert any("evil" in k for k in r.errors)
    assert list(env.root.rglob("evil*")) == []
    assert "central_project_skipped" in actions(env)


def test_schema_invalid_project_is_skipped_others_apply_and_old_version_stays(env):
    register(env)
    pull(env, project_entry(lang="eng"))
    env.svc.sync_once()
    broken = project_entry(version=2)
    del broken["config"]["qc"]                       # newer server, incompatible
    pull(env, broken, project_entry(pid=PID2, name="Good"))
    r = env.svc.sync_once()
    assert PID in r.errors and r.applied == [PID2]
    assert env.pm.load(PID)["version"] == 1          # previous local version intact
    assert env.db.get_central_project(PID)["central_version"] == 1
    assert env.db.get_central_project(PID)["managed"] == 1   # not "unassigned"
    assert "with 1 skipped" in env.state.settings.last_sync_status


def test_garbage_entry_does_not_crash_or_unassign(env):
    register(env)
    pull(env, project_entry())
    env.svc.sync_once()
    pull(env, "garbage", {"no": "id"}, project_entry(version=1))
    r = env.svc.sync_once()
    assert r.status == "ok" and r.unassigned == [] and r.unchanged == 1


# ---- failures --------------------------------------------------------------------------------
def test_unavailable_keeps_everything_and_reports(env):
    register(env)
    pull(env, project_entry())
    env.svc.sync_once()
    env.holder["client"] = FakeClient(exc=CentralUnavailable("dns failure"))
    r = env.svc.sync_once()
    assert r.status == "unavailable" and "dns failure" in r.message
    assert env.pm.load(PID)["version"] == 1
    assert env.db.get_central_project(PID)["managed"] == 1
    assert "unavailable" in env.state.settings.last_sync_status
    assert env.state.settings.auth_failed is False


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failure_is_persisted_and_stops_further_calls(env, status):
    register(env)
    env.holder["client"] = FakeClient(exc=CentralAuthError(status))
    r = env.svc.sync_once()
    assert r.status == "auth_failed"
    assert env.state.settings.auth_failed is True
    assert "central_auth_failed" in actions(env)
    client2 = FakeClient(pull=PullResult("pc-1", 600, []))
    env.holder["client"] = client2
    r2 = env.svc.sync_once()
    assert r2.status == "auth_failed" and client2.pulls == []     # no hammering
    assert CentralState(env.root).settings.auth_failed is True    # survives restart


def test_missing_api_key_is_treated_as_unregistered(env):
    register(env)
    (env.root / "keys" / "central_api_key.bin").unlink()
    assert env.svc.sync_once().status == "not_registered"


def test_unexpected_client_exception_is_reported_not_raised(env):
    register(env)
    env.holder["client"] = FakeClient(exc=RuntimeError("boom"))
    r = env.svc.sync_once()
    assert r.status == "unavailable" and "boom" in r.message


# ---- register / disconnect -----------------------------------------------------------------------
def test_register_success_stores_state_and_audits(env):
    env.holder["client"] = FakeClient(register=RegisterResult("pc-9", "dsk_NEW", 120))
    env.svc.register("https://c.example/", "ABCDEFGH1234")
    s = env.state
    assert s.is_registered() and s.api_key() == "dsk_NEW"
    assert s.settings.pc_id == "pc-9" and s.settings.poll_interval_seconds == 120
    assert s.settings.server_url == "https://c.example"
    assert "central_registered" in actions(env)
    # the key itself must never reach the audit log
    assert "dsk_NEW" not in json.dumps([dict(r) for r in env.db.conn.execute("SELECT * FROM audit_log")])


def test_register_clears_auth_failed(env):
    register(env)
    env.state.update(auth_failed=True)
    env.holder["client"] = FakeClient(register=RegisterResult("pc-1", "dsk_NEW2", 600))
    env.svc.register("https://c.example", "ABCDEFGH1234")
    assert env.state.settings.auth_failed is False and env.state.api_key() == "dsk_NEW2"


def test_register_failure_leaves_state_untouched(env):
    env.holder["client"] = FakeClient(register=CentralRegistrationError("bad"))
    with pytest.raises(CentralRegistrationError):
        env.svc.register("https://c.example", "ABCDEFGH1234")
    assert not env.state.is_registered()


def test_register_rejects_non_https_url_before_any_call(env, monkeypatch):
    from core.central_sync import default_client_factory

    def no_network(*a, **k):
        raise AssertionError("a transport/opener was built for a non-https URL")
    monkeypatch.setattr("core.central_client.urllib.request.build_opener",
                        no_network)
    env.svc._client_factory = default_client_factory
    with pytest.raises(ValueError):
        env.svc.register("http://central.example", "ABCDEFGH1234")
    assert not env.state.is_registered()


def test_disconnect_unmanages_projects_but_keeps_them(env):
    register(env)
    pull(env, project_entry())
    env.svc.sync_once()
    env.svc.disconnect()
    assert not env.state.is_registered() and env.state.api_key() is None
    assert env.db.get_central_project(PID)["managed"] == 0
    assert env.pm.load(PID)                                      # local data kept
    assert "central_disconnected" in actions(env)
    assert env.svc.sync_once().status == "not_registered"


# ---- Task 5 rulings ---------------------------------------------------------------------
class HookClient(FakeClient):
    """Runs a callback inside pull_config (a concurrent UI-thread action)."""

    def __init__(self, hook, **kw):
        super().__init__(**kw)
        self.hook = hook

    def pull_config(self, pc_id, api_key, operator_id=""):
        self.hook()
        return super().pull_config(pc_id, api_key, operator_id)


def test_reregister_during_auth_failure_does_not_flag_the_new_registration(env):
    register(env)

    def reregister():
        env.state.register_result("https://c.example", "pc-1", "dsk_NEWKEY", 600)
    env.holder["client"] = HookClient(reregister, exc=CentralAuthError(401))
    r = env.svc.sync_once()
    assert r.status == "unavailable" and "registration changed" in r.message
    assert env.state.settings.auth_failed is False
    assert env.state.is_registered() and env.state.api_key() == "dsk_NEWKEY"
    assert env.state.settings.last_sync_status == ""
    assert "central_auth_failed" not in actions(env)


def test_reregister_during_successful_pull_applies_nothing(env):
    register(env)

    def reregister():
        env.state.register_result("https://c.example", "pc-1", "dsk_NEWKEY", 600)
    env.holder["client"] = HookClient(
        reregister, pull=PullResult("pc-1", 90, [project_entry()]))
    r = env.svc.sync_once()
    assert r.status == "unavailable" and "registration changed" in r.message
    assert env.db.get_central_project(PID) is None and env.db.get_project(PID) is None
    assert env.state.settings.poll_interval_seconds == 600


def test_disconnect_during_pull_resurrects_nothing(env):
    register(env)
    env.holder["client"] = HookClient(
        env.state.clear, pull=PullResult("pc-1", 90, [project_entry()]))
    r = env.svc.sync_once()
    assert r.status == "unavailable" and "registration changed" in r.message
    assert not (env.root / "central.json").exists()
    assert env.db.get_central_project(PID) is None


def test_disconnect_during_auth_failure_resurrects_nothing(env):
    register(env)
    env.holder["client"] = HookClient(env.state.clear, exc=CentralAuthError(403))
    r = env.svc.sync_once()
    assert r.status == "unavailable"
    assert not (env.root / "central.json").exists()
    assert "central_auth_failed" not in actions(env)


def test_server_echoing_a_different_pc_id_is_rejected(env):
    register(env)
    env.holder["client"] = FakeClient(
        pull=PullResult("pc-OTHER", 600, [project_entry()]))
    r = env.svc.sync_once()
    assert r.status == "unavailable" and "different PC" in r.message
    assert env.db.get_central_project(PID) is None
    assert env.state.settings.last_sync_status.startswith("unavailable")


def test_thousands_of_projects_are_capped_and_reported(env):
    register(env)
    pull(env, project_entry(), project_entry(pid=PID2, name="Other"))
    env.svc.sync_once()
    junk = [{"project_id": f"junk-{i}"} for i in range(5000)]
    # PID2 sits beyond the cap: it must not be mistaken for "unassigned"
    pull(env, project_entry(version=1), *junk, project_entry(pid=PID2, name="Other"))
    r = env.svc.sync_once()
    assert r.status == "ok" and "overflow" in r.errors
    assert len(r.errors) == 500          # 499 junk entries + the overflow key
    assert r.unassigned == []
    assert env.db.get_central_project(PID2)["managed"] == 1
    assert env.state.settings.last_sync_status == "ok with 500 skipped"


def test_hostile_ids_and_reasons_are_truncated_and_stripped(env, caplog):
    register(env)
    hostile = "A\nforged log line\r\x1b[31m" + "x" * 10000
    entry = project_entry()
    entry["project_id"] = hostile
    with caplog.at_level(logging.DEBUG):
        pull(env, entry)
        r = env.svc.sync_once()
    assert r.errors
    for k, v in r.errors.items():
        assert len(k) <= 200 and len(v) <= 200
        assert not any(ord(c) < 32 or ord(c) == 127 for c in k + v)
    rows = [json.loads(x["detail"]) for x in env.db.conn.execute(
        "SELECT detail FROM audit_log WHERE action='central_project_skipped'")]
    assert rows
    for d in rows:
        for v in d.values():
            assert len(v) <= 200
            assert not any(ord(c) < 32 or ord(c) == 127 for c in v)
    skipped_logs = [rec.getMessage() for rec in caplog.records
                    if "skipped central project" in rec.getMessage()]
    assert skipped_logs
    for msg in skipped_logs:
        assert "\n" not in msg and "\x1b" not in msg and len(msg) < 500


def test_hostile_unavailable_message_is_sanitised(env):
    register(env)
    env.holder["client"] = FakeClient(
        exc=CentralUnavailable("bad\nline " + "y" * 10000))
    r = env.svc.sync_once()
    assert len(r.message) <= 200 and "\n" not in r.message
    assert len(env.state.settings.last_sync_status) <= 200


def test_reregister_during_unavailable_does_not_record_status(env):
    register(env)

    def reregister():
        env.state.register_result("https://c.example", "pc-1", "dsk_NEWKEY", 600)
    env.holder["client"] = HookClient(reregister, exc=CentralUnavailable("down"))
    r = env.svc.sync_once()
    assert r.status == "unavailable" and "registration changed" in r.message
    assert env.state.settings.last_sync_status == ""
    assert env.state.settings.last_sync_at == ""


def test_disconnect_while_applying_does_not_resurrect_state(env, monkeypatch):
    register(env)
    pull(env, project_entry())
    real_adopt = env.pm.adopt

    def adopt_then_disconnect(cfg):
        out = real_adopt(cfg)
        env.state.clear()
        return out
    monkeypatch.setattr(env.pm, "adopt", adopt_then_disconnect)
    r = env.svc.sync_once()
    assert r.status == "unavailable" and "registration changed" in r.message
    assert not (env.root / "central.json").exists()
    assert not env.state.is_registered()


# ---- fix round: lock, generation, never-raise -----------------------------------------
def three_projects():
    return [project_entry(), project_entry(pid=PID2, name="Two"),
            project_entry(pid="aaaaaaaa-2222-3333-4444-555555555555", name="Three")]


class SignalLock:
    """Wraps the service lock; the thread named "action" announces that it is
    about to take it, so a test can wait for that instead of sleeping."""

    def __init__(self, real, attempt):
        self.real, self.attempt = real, attempt

    def __enter__(self):
        if threading.current_thread().name == "action":
            self.attempt.set()
        return self.real.__enter__()

    def __exit__(self, *exc):
        return self.real.__exit__(*exc)


def sync_with_concurrent_action(env, action):
    """Run sync_once on a worker thread; once its apply loop is inside the
    first pm.adopt, start `action` on another thread and wait until that
    thread is about to take the service lock, then let the apply continue.
    Returns the SyncResult."""
    in_apply, attempt = threading.Event(), threading.Event()
    env.svc._lock = SignalLock(env.svc._lock, attempt)
    real_adopt, first = env.pm.adopt, [True]

    def spy(cfg):
        if first[0]:
            first[0] = False
            in_apply.set()
            assert attempt.wait(5), "action thread never reached the lock"
        return real_adopt(cfg)
    env.pm.adopt = spy
    out = {}
    worker = threading.Thread(target=lambda: out.update(r=env.svc.sync_once()))
    worker.start()
    assert in_apply.wait(5), "apply loop never started"
    other = threading.Thread(target=action, name="action")
    other.start()
    worker.join(10)
    other.join(10)
    assert not worker.is_alive() and not other.is_alive()
    return out["r"]


def test_disconnect_from_another_thread_during_apply_leaves_nothing_managed(env):
    register(env)
    pull(env, *three_projects())
    r = sync_with_concurrent_action(env, env.svc.disconnect)
    assert r.status == "ok" and len(r.applied) == 3     # disconnect waited
    assert env.db.list_central_projects(managed_only=True) == []
    assert not env.state.is_registered()
    assert not (env.root / "central.json").exists()
    assert "central_disconnected" in actions(env)


def test_register_from_another_thread_during_apply_keeps_new_identity_clean(env):
    register(env)
    env.holder["client"] = FakeClient(
        pull=PullResult("pc-1", 600, three_projects()),
        register=RegisterResult("pc-2", "dsk_NEW", 300))
    r = sync_with_concurrent_action(
        env, lambda: env.svc.register("https://c.example", "ABCDEFGH1234"))
    assert r.status == "ok" and len(r.applied) == 3     # register waited
    s = env.state.settings
    assert s.pc_id == "pc-2" and env.state.api_key() == "dsk_NEW"
    assert s.auth_failed is False and s.poll_interval_seconds == 300
    env.holder["client"] = FakeClient(pull=PullResult("pc-2", 300, []))
    assert env.svc.sync_once().status == "ok"
    assert env.db.list_central_projects(managed_only=True) == []


# ---- sync_once never raises ------------------------------------------------------------
def deny_save(self, settings=None):
    raise PermissionError("held by antivirus")


@pytest.mark.parametrize("client", [
    FakeClient(pull=PullResult("pc-1", 600, [])),             # ok path
    FakeClient(exc=CentralUnavailable("down")),               # unavailable path
    FakeClient(exc=CentralAuthError(401)),                    # auth-failed path
], ids=["ok", "unavailable", "auth_failed"])
def test_sync_once_never_raises_when_state_cannot_be_saved(env, monkeypatch, client):
    register(env)
    env.holder["client"] = client
    monkeypatch.setattr(CentralState, "_save", deny_save)
    r = env.svc.sync_once()
    assert isinstance(r, SyncResult) and r.status == "unavailable"
    assert "PermissionError" in r.message


def test_sync_once_never_raises_when_the_database_fails(env, monkeypatch):
    register(env)

    def broken(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(env.db, "list_central_projects", broken)
    r = env.svc.sync_once()
    assert isinstance(r, SyncResult) and r.status == "unavailable"
    assert "OperationalError" in r.message


# ---- unambiguous skip keys --------------------------------------------------------------
def test_entries_without_a_usable_id_are_keyed_by_index_and_all_counted(env):
    register(env)
    pull(env, "garbage", {"no": "id"}, {"project_id": ""}, project_entry())
    r = env.svc.sync_once()
    assert sorted(r.errors) == ["invalid-entry-0", "invalid-entry-1",
                                "invalid-entry-2"]
    assert env.state.settings.last_sync_status == "ok with 3 skipped"


def test_registration_cleared_mid_loop_stops_applying_further_projects(env):
    register(env)
    pull(env, *three_projects())
    real_adopt, calls = env.pm.adopt, []

    def adopt_then_clear(cfg):
        calls.append(cfg["project_id"])
        out = real_adopt(cfg)
        env.state.clear()
        return out
    env.pm.adopt = adopt_then_clear
    r = env.svc.sync_once()
    assert r.status == "unavailable" and r.applied == []
    assert calls == [PID]
    assert env.db.get_project(PID2) is None


# ---- register's network call is made outside the service lock -----------------------------
class GatedRegisterClient(FakeClient):
    """register() blocks until `release` is set (a slow HTTP enrollment)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.inside, self.release = threading.Event(), threading.Event()

    def register(self, code):
        self.inside.set()
        assert self.release.wait(5), "test never released the register call"
        return super().register(code)


def start_slow_register(env, client):
    env.holder["client"] = client
    t = threading.Thread(target=lambda: env.svc.register(
        "https://c.example", "ABCDEFGH1234"), name="action")
    t.start()
    assert client.inside.wait(5), "register never reached the network call"
    return t


def test_service_lock_is_free_while_register_waits_on_the_network(env):
    client = GatedRegisterClient(register=RegisterResult("pc-9", "dsk_NEW", 120))
    t = start_slow_register(env, client)
    try:
        assert env.svc._lock.acquire(timeout=1), "lock held during the HTTP call"
        env.svc._lock.release()
    finally:
        client.release.set()
        t.join(10)
    assert not t.is_alive()
    assert env.state.is_registered() and env.state.settings.pc_id == "pc-9"


def test_sync_once_is_not_held_up_by_a_slow_register(env):
    register(env)
    client = GatedRegisterClient(pull=PullResult("pc-1", 600, []),
                                 register=RegisterResult("pc-2", "dsk_NEW", 120))
    t = start_slow_register(env, client)
    try:
        out = {}
        w = threading.Thread(target=lambda: out.update(r=env.svc.sync_once()))
        started = time.monotonic()
        w.start()
        w.join(2)
        assert not w.is_alive(), "sync_once waited for the enrollment HTTP call"
        assert time.monotonic() - started < 2
        assert out["r"].status == "ok"
        assert not client.release.is_set()
    finally:
        client.release.set()
        t.join(10)
    assert env.state.settings.pc_id == "pc-2"


# ---- C1a: new managed projects are seeded from the PC's previous project ------------------
def bank_project(env, name="Bank local"):
    """A project the PC already had before enrollment, with real local setup."""
    cfg = env.pm.create(name)
    cfg["export"]["output_path"] = "D:/bank/out"
    cfg["delivery"]["local_folder"]["path"] = "D:/bank/delivered"
    cfg["delivery"]["sftp"] = {"enabled": True, "host": "sftp.bank.lk",
                               "username": "scan", "password": "pw"}
    cfg["scanner"]["sdk"] = "twain"
    return env.pm.save(cfg)


def test_new_managed_project_is_seeded_from_the_pcs_existing_local_project(env):
    source = bank_project(env)
    register(env)
    pull(env, project_entry())
    env.svc.sync_once()
    managed, stored = env.pm.load(PID), env.pm.load(source["project_id"])
    for key in ("scanner", "export", "delivery"):
        assert managed[key] == stored[key]
    assert managed["export"]["output_path"] == "D:/bank/out"
    assert managed["delivery"]["sftp"]["host"] == "sftp.bank.lk"
    assert managed["scanner"]["sdk"] == "twain"


def test_all_new_projects_in_one_pull_get_the_same_seed(env):
    bank_project(env)
    register(env)
    pull(env, project_entry(), project_entry(pid=PID2, name="Other"))
    env.svc.sync_once()
    assert env.pm.load(PID)["export"] == env.pm.load(PID2)["export"]
    assert env.pm.load(PID2)["export"]["output_path"] == "D:/bank/out"


def test_seed_source_is_the_first_active_unmanaged_project_by_name(env):
    bank_project(env, "Zulu")
    first = bank_project(env, "Alpha")
    first["export"]["output_path"] = "D:/alpha"
    env.pm.save(first)
    archived = bank_project(env, "Aaa archived")
    archived["status"] = "archived"
    archived["export"]["output_path"] = "D:/archived"
    env.pm.save(archived)
    register(env)
    pull(env, project_entry())
    env.svc.sync_once()
    assert env.pm.load(PID)["export"]["output_path"] == "D:/alpha"


def test_no_local_project_falls_back_to_defaults(env):
    register(env)
    pull(env, project_entry())
    env.svc.sync_once()
    cfg = env.pm.load(PID)
    expected = default_local_sections(env.root)
    for key in ("scanner", "export", "delivery"):
        assert cfg[key] == expected[key]


def test_a_centrally_managed_project_is_never_the_seed_source(env):
    managed = bank_project(env)
    env.db.set_central_project(managed["project_id"], 1)     # already managed
    register(env)
    pull(env, project_entry(),
         project_entry(pid=managed["project_id"], name="Bank local"))
    env.svc.sync_once()
    cfg = env.pm.load(PID)
    assert cfg["export"]["output_path"] == str(env.root / "output")
    assert cfg["delivery"]["sftp"] == {"enabled": False}


def test_seed_is_a_deep_copy_of_the_source(env):
    source = bank_project(env)
    register(env)
    pull(env, project_entry())
    env.svc.sync_once()
    managed = env.pm.load(PID)
    managed["delivery"]["sftp"]["host"] = "changed.example"
    managed["export"]["output_path"] = "D:/changed"
    env.pm.save(managed)
    stored = env.pm.load(source["project_id"])
    assert stored["delivery"]["sftp"]["host"] == "sftp.bank.lk"
    assert stored["export"]["output_path"] == "D:/bank/out"


def test_after_enrollment_and_restart_the_pipeline_keeps_the_pcs_delivery_settings(env):
    source = bank_project(env)
    register(env)
    pull(env, project_entry(lang="deu"))
    env.svc.sync_once()
    profile = choose_pipeline_profile(env.db, fallback=lambda: {"fallback": True})
    assert profile["project_id"] == PID and profile["ocr"]["lang"] == "deu"
    stored = env.pm.load(source["project_id"])
    assert profile["export"] == stored["export"]
    assert profile["delivery"] == stored["delivery"]
    assert profile["scanner"] == stored["scanner"]
    assert profile["export"]["output_path"] == "D:/bank/out"


def test_failing_file_write_for_one_project_is_reported_others_apply(env, monkeypatch):
    import core.profile_manager as profile_manager
    monkeypatch.setattr(profile_manager, "REPLACE_RETRY_DELAY", 0.0)
    real_replace = profile_manager.os.replace

    def replace(src, dst):
        if PID in os.path.basename(dst):
            raise PermissionError("locked by another process")
        return real_replace(src, dst)

    monkeypatch.setattr(profile_manager.os, "replace", replace)
    register(env)
    pull(env, project_entry(pid=PID), project_entry(pid=PID2, name="Good"))
    r = env.svc.sync_once()
    assert PID in r.errors and r.applied == [PID2]
    assert env.pm.load(PID2)["name"] == "Good"
    assert not env.pm._path(PID).exists()
    assert list(env.pm.projects_dir.glob("*.tmp")) == []
