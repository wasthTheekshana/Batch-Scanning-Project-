import copy
import logging
import threading

import pytest
from PyQt6.QtCore import QObject, pyqtSlot

import sync.central_sync
from sync.central_state import CentralState
from sync.central_sync import (RETRY_AFTER_FAILURE, CentralSyncService,
                               SyncResult, SyncWorker, build_central,
                               choose_pipeline_profile, shutdown_central,
                               startup_sync)
from ui.event_bus import EventBus
from projects.profile_manager import DEFAULT_PROJECT, ProfileManager
from data.db import Database


class FakeService:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0
        self.called = threading.Event()

    def sync_once(self):
        self.calls += 1
        self.called.set()
        r = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture()
def state(tmp_path):
    st = CentralState(tmp_path)
    st.register_result("https://c.example", "pc-1", "k", 600)
    return st


def worker(service, state, on_result=None, **kw):
    return SyncWorker(service, state, on_result=on_result, **kw)


# ---- next_wait (pure) ---------------------------------------------------------------
def test_next_wait_policy(state):
    w = worker(FakeService([SyncResult("ok")]), state)
    assert w.next_wait(SyncResult("ok")) == 600.0
    assert w.next_wait(SyncResult("unavailable")) == RETRY_AFTER_FAILURE
    state.update(poll_interval_seconds=60)
    assert w.next_wait(SyncResult("unavailable")) == 60.0          # min(interval, retry)
    assert w.next_wait(SyncResult("auth_failed")) is None
    assert w.next_wait(SyncResult("not_registered")) is None
    assert RETRY_AFTER_FAILURE >= 120.0


def test_next_wait_unavailable_is_capped_by_retry_constant(state):
    w = worker(FakeService([SyncResult("ok")]), state)
    state.update(poll_interval_seconds=3600)
    assert w.next_wait(SyncResult("unavailable")) == RETRY_AFTER_FAILURE
    assert w.next_wait(SyncResult("ok")) == 3600.0


def test_next_wait_before_first_sync(state, tmp_path):
    w = worker(FakeService([SyncResult("ok")]), state)
    assert w.next_wait(None) == 600.0
    state.update(auth_failed=True)
    assert w.next_wait(None) is None
    unregistered = CentralState(tmp_path / "other")
    assert worker(FakeService([SyncResult("ok")]), unregistered).next_wait(None) is None


# ---- thread behaviour -----------------------------------------------------------------
def test_initial_sync_runs_once_then_waits(state):
    svc = FakeService([SyncResult("ok")])
    got = []
    delivered = threading.Event()
    w = worker(svc, state, on_result=lambda r: (got.append(r), delivered.set()))
    w.start(initial_sync=True)
    try:
        assert delivered.wait(5)
        assert got[0].status == "ok"
        delivered.clear()
        assert not delivered.wait(0.3) and svc.calls == 1   # now waits for the poll interval
    finally:
        w.stop()


def test_initial_sync_false_does_not_sync_until_woken(state):
    svc = FakeService([SyncResult("ok")])
    w = worker(svc, state)
    w.start(initial_sync=False)
    try:
        assert not svc.called.wait(0.3)
        w.sync_now()
        assert svc.called.wait(5)
    finally:
        w.stop()


def test_sync_now_triggers_another_sync(state):
    svc = FakeService([SyncResult("ok")])
    w = worker(svc, state)
    w.start(initial_sync=True)
    try:
        assert svc.called.wait(5)
        svc.called.clear()
        w.sync_now()
        assert svc.called.wait(5) and svc.calls == 2
    finally:
        w.stop()


def test_auth_failed_stops_polling_until_woken(state):
    svc = FakeService([SyncResult("auth_failed")])
    w = worker(svc, state)
    w.start(initial_sync=True)
    try:
        assert svc.called.wait(5)
        svc.called.clear()
        assert not svc.called.wait(0.5)         # no timer-driven retry
        w.sync_now()
        assert svc.called.wait(5)
    finally:
        w.stop()


def test_exceptions_in_service_and_callback_never_kill_the_thread(state):
    svc = FakeService([RuntimeError("boom"), SyncResult("ok")])
    got = []
    delivered = threading.Event()

    def bad_callback(r):
        got.append(r)
        delivered.set()
        raise RuntimeError("ui exploded")

    w = worker(svc, state, on_result=bad_callback)
    w.start(initial_sync=True)
    try:
        assert delivered.wait(5)                # callback ran after the service raised
        assert got[0].status == "unavailable" and "boom" in got[0].message
        delivered.clear()
        w.sync_now()
        assert delivered.wait(5)                # thread survived both exceptions
        assert got[1].status == "ok" and svc.calls == 2
    finally:
        w.stop()


def test_stop_is_prompt_and_idempotent(state):
    svc = FakeService([SyncResult("ok")])
    w = worker(svc, state)
    w.start(initial_sync=True)
    assert svc.called.wait(5)
    w.stop()
    w.stop()
    assert not w.is_running()


def test_stop_before_start_is_safe(state):
    worker(FakeService([SyncResult("ok")]), state).stop()


class BlockingService:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def sync_once(self):
        self.entered.set()
        self.release.wait(10)
        return SyncResult("ok")


def test_stop_returns_promptly_when_a_sync_is_blocked(state):
    svc = BlockingService()
    w = worker(svc, state, join_timeout=0.2)
    w.start(initial_sync=True)
    try:
        assert svc.entered.wait(5)
        w.stop()                                # must not wait for the 10 s block
        assert w.is_running()                   # daemon thread still stuck in sync
    finally:
        svc.release.set()
    w._thread.join(5)
    assert not w.is_running()


# ---- cross-thread delivery through the event bus -----------------------------------------
class Receiver(QObject):
    def __init__(self):
        super().__init__()
        self.threads = []
        self.results = []

    @pyqtSlot(object)
    def on_synced(self, result):
        self.threads.append(threading.current_thread())
        self.results.append(result)


def test_central_synced_from_worker_thread_is_delivered_on_main_thread(qtbot, state):
    bus = EventBus()
    recv = Receiver()
    bus.central_synced.connect(recv.on_synced)
    w = worker(FakeService([SyncResult("ok", applied=["p1"])]), state,
               on_result=bus.central_synced.emit)
    w.start(initial_sync=True)
    try:
        qtbot.waitUntil(lambda: bool(recv.results), timeout=5000)
        assert recv.results[0].applied == ["p1"]
        assert recv.threads[0] is threading.main_thread()
        assert w._thread is not threading.main_thread()
    finally:
        w.stop()


# ---- startup_sync -------------------------------------------------------------------------
class TimeoutService:
    def __init__(self, result=None, exc=None):
        self.timeout = 15.0
        self.calls = []
        self._result, self._exc = result, exc

    def sync_once(self):
        self.calls.append(self.timeout)
        if self._exc is not None:
            raise self._exc
        return self._result


def test_startup_sync_skips_when_not_registered(tmp_path):
    svc = TimeoutService(SyncResult("ok"))
    assert startup_sync(svc, CentralState(tmp_path / "none")) is None
    assert svc.calls == []
    assert svc.timeout == 15.0


def test_startup_sync_uses_short_timeout_and_restores_it(state):
    svc = TimeoutService(SyncResult("ok"))
    result = startup_sync(svc, state)
    assert result.status == "ok"
    assert svc.calls == [5.0]
    assert svc.timeout == 15.0


def test_startup_sync_restores_timeout_and_swallows_when_sync_raises(state):
    svc = TimeoutService(exc=RuntimeError("kaput"))
    assert startup_sync(svc, state) is None
    assert svc.calls == [5.0]
    assert svc.timeout == 15.0


# ---- choose_pipeline_profile -----------------------------------------------------------
@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "w.db")
    yield d
    d.close()


def _project(pm, pid, name):
    cfg = copy.deepcopy(DEFAULT_PROJECT)
    cfg.update(project_id=pid, name=name, created="2026-10-09T00:00:00+00:00")
    cfg["rules"]["rule_set_id"] = "r"
    return pm.adopt(cfg)


def test_choose_profile_prefers_first_managed_project(db, tmp_path):
    pm = ProfileManager(tmp_path / "p", db)
    _project(pm, "aaaa", "Alpha local")
    _project(pm, "bbbb", "Zulu central")
    db.set_central_project("bbbb", 1)
    assert choose_pipeline_profile(db, fallback=lambda: {"x": 1})["project_id"] == "bbbb"


def test_choose_profile_ignores_unmanaged_and_falls_back(db, tmp_path):
    pm = ProfileManager(tmp_path / "p", db)
    _project(pm, "aaaa", "Alpha")
    db.set_central_project("aaaa", 1, managed=False)
    assert choose_pipeline_profile(db, fallback=lambda: {"fallback": True}) == {"fallback": True}


def test_choose_profile_skips_inactive_managed_projects(db, tmp_path):
    pm = ProfileManager(tmp_path / "p", db)
    cfg = _project(pm, "aaaa", "Alpha")
    cfg["status"] = "archived"
    pm.save(cfg)
    db.set_central_project("aaaa", 1)
    assert choose_pipeline_profile(db, fallback=lambda: {"fb": 1}) == {"fb": 1}


# ---- I2: an unavailable startup sync is retried soon, not after the whole interval -------
def test_first_unavailable_result_waits_at_most_two_minutes_even_with_long_interval(state):
    state.update(poll_interval_seconds=86400)
    w = worker(FakeService([SyncResult("ok")]), state)
    assert w.next_wait(SyncResult("unavailable")) <= 120.0
    assert w.next_wait(None) == 86400.0                   # without a result: unchanged


def test_unavailable_first_result_triggers_a_retry_without_sync_now(state, monkeypatch):
    monkeypatch.setattr(sync.central_sync, "RETRY_AFTER_FAILURE", 0.05)
    svc = FakeService([SyncResult("ok")])
    w = worker(svc, state)
    w.start(initial_sync=False, first_result=SyncResult("unavailable"))
    try:
        assert svc.called.wait(3)                          # timer-driven, nobody woke it
    finally:
        w.stop()


@pytest.mark.parametrize("first", [None, SyncResult("ok")], ids=["none", "ok"])
def test_no_first_result_or_ok_result_keeps_waiting_for_the_interval(state, monkeypatch, first):
    monkeypatch.setattr(sync.central_sync, "RETRY_AFTER_FAILURE", 0.05)
    svc = FakeService([SyncResult("ok")])
    w = worker(svc, state)
    w.start(initial_sync=False, first_result=first)
    try:
        assert not svc.called.wait(0.4)                    # interval is 600 s
        w.sync_now()
        assert svc.called.wait(3)
    finally:
        w.stop()


# ---- I3: building the central objects can never stop the app --------------------------
class BusStub:
    def __init__(self):
        self.central_synced = type("S", (), {"emit": staticmethod(lambda r: None)})


@pytest.fixture()
def pm_db(tmp_path):
    d = Database(tmp_path / "b.db")
    yield d, ProfileManager(tmp_path / "p", d)
    d.close()


def test_build_central_returns_wired_objects(tmp_path, pm_db):
    db, pm = pm_db
    bus = BusStub()
    st, svc, w = build_central(db, pm, tmp_path, None, bus, lambda: "bob")
    assert isinstance(st, CentralState) and isinstance(svc, CentralSyncService)
    assert isinstance(w, SyncWorker) and w._on_result is bus.central_synced.emit
    assert svc._operator_fn() == "bob" and svc.state is st


@pytest.mark.parametrize("target", ["CentralState", "CentralSyncService", "SyncWorker"])
def test_build_central_failure_returns_nones_and_logs(tmp_path, pm_db, monkeypatch,
                                                      caplog, target):
    db, pm = pm_db

    def boom(*a, **k):
        raise OSError("disk on fire")
    monkeypatch.setattr(sync.central_sync, target, boom)
    with caplog.at_level(logging.ERROR, logger="sync.central_sync"):
        assert build_central(db, pm, tmp_path, None, BusStub(), lambda: "") ==             (None, None, None)
    assert "central sync disabled this session" in caplog.text


def test_shutdown_central_stops_the_worker_only_if_it_exists():
    shutdown_central(None)                                  # must not raise
    stopped = []
    shutdown_central(type("W", (), {"stop": lambda self: stopped.append(1)})())
    assert stopped == [1]
