"""The Workflow Designer follows projects that arrive from a central sync
without ever discarding the operator's unsaved edits."""
import copy
import logging
import sqlite3
import threading

import pytest
from PyQt6.QtCore import QThread
from PyQt6.QtTest import QSignalSpy

from core.central_sync import SyncResult
from core.event_bus import EventBus
from core.profile_manager import DEFAULT_PROJECT, ProfileManager
from core.rbac import User
from data.db import Database
from ui.workflow_designer import WorkflowDesigner

ADMIN = User("u1", "root", "admin")
BANNER_NOTICE_TEXT = "Updated by the central server"


@pytest.fixture()
def env(tmp_path):
    db = Database(tmp_path / "d.db")
    pm = ProfileManager(tmp_path / "projects", db)
    yield type("E", (), dict(db=db, pm=pm))
    db.close()


def _adopt(env, name, pid, managed=True):
    cfg = copy.deepcopy(DEFAULT_PROJECT)
    cfg.update(project_id=pid, name=name, created="2026-10-09T00:00:00+00:00")
    cfg["rules"]["rule_set_id"] = "r"
    env.pm.adopt(cfg)
    env.db.set_central_project(pid, 1, managed=managed)
    return pid


def _designer(qtbot, env, bus):
    d = WorkflowDesigner(env.db, env.pm, ADMIN, bus=bus)
    qtbot.addWidget(d)
    return d


def _items(d):
    return [d.project_combo.itemData(i) for i in range(d.project_combo.count())]


def _store_new_delivery_path(env, pid, path):
    cfg = env.pm.load(pid)
    cfg["delivery"]["local_folder"]["path"] = path
    env.pm.save(cfg)


def test_project_adopted_after_the_designer_was_built_appears_in_the_combo(qtbot, env):
    bus = EventBus()
    local = env.pm.create("Alpha local")
    d = _designer(qtbot, env, bus)
    assert _items(d) == [local["project_id"]]
    _adopt(env, "Zulu synced", "zzzz")
    bus.central_synced.emit(SyncResult("ok", applied=["zzzz"]))
    assert _items(d) == [local["project_id"], "zzzz"]
    assert d.project_combo.itemText(1) == "Zulu synced (v1)"
    assert d.project_combo.currentData() == local["project_id"]


def test_unsaved_edit_survives_a_sync_that_adds_another_project(qtbot, env):
    bus = EventBus()
    local = env.pm.create("Alpha local")
    d = _designer(qtbot, env, bus)
    d.delivery_path.setText("D:/typed/not/saved")
    spy = QSignalSpy(d.project_combo.currentIndexChanged)
    _adopt(env, "Zulu synced", "zzzz")
    bus.central_synced.emit(SyncResult("ok", applied=["zzzz"]))
    assert "zzzz" in _items(d)
    assert d.delivery_path.text() == "D:/typed/not/saved"
    assert d.project_combo.currentData() == local["project_id"]
    assert len(spy) == 0


def test_selected_project_in_applied_shows_a_notice_and_keeps_the_form(qtbot, env):
    bus = EventBus()
    pid = _adopt(env, "Invoices", "aaaa")
    d = _designer(qtbot, env, bus)
    assert d.central_notice.isHidden()
    d.delivery_path.setText("D:/typed/not/saved")
    _store_new_delivery_path(env, pid, "D:/from/central")
    bus.central_synced.emit(SyncResult("ok", applied=[pid]))
    assert not d.central_notice.isHidden()
    assert BANNER_NOTICE_TEXT in d.central_notice.text()
    assert d.delivery_path.text() == "D:/typed/not/saved"
    d.load_selected()                                    # re-selection
    assert d.central_notice.isHidden()
    assert d.delivery_path.text() == "D:/from/central"


def test_notice_is_not_shown_when_another_project_was_applied(qtbot, env):
    bus = EventBus()
    local = env.pm.create("Alpha local")
    d = _designer(qtbot, env, bus)
    _adopt(env, "Zulu synced", "zzzz")
    bus.central_synced.emit(SyncResult("ok", applied=["zzzz"]))
    assert d.project_combo.currentData() == local["project_id"]
    assert d.central_notice.isHidden()


def test_selected_project_in_unassigned_becomes_editable_without_reloading(qtbot, env):
    bus = EventBus()
    pid = _adopt(env, "Invoices", "aaaa")
    d = _designer(qtbot, env, bus)
    assert not d.managed_banner.isHidden()
    assert not any(b.isEnabled() for b in d._central_boxes())
    d.delivery_path.setText("D:/typed/not/saved")
    env.db.unmanage_central_project(pid)
    bus.central_synced.emit(SyncResult("ok", unassigned=[pid]))
    assert d.managed_banner.isHidden()
    assert all(b.isEnabled() for b in d._central_boxes())
    assert d.delivery_path.text() == "D:/typed/not/saved"


def test_result_with_nothing_applied_or_unassigned_does_not_touch_the_ui(qtbot, env, monkeypatch):
    bus = EventBus()
    local = env.pm.create("Alpha local")
    d = _designer(qtbot, env, bus)
    d.delivery_path.setText("D:/typed/not/saved")
    _adopt(env, "Zulu synced", "zzzz")                   # exists, but no event announced it
    calls = []
    real = env.db.list_projects
    monkeypatch.setattr(env.db, "list_projects",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    spy = QSignalSpy(d.project_combo.currentIndexChanged)
    bus.central_synced.emit(SyncResult("ok", unchanged=3))
    bus.central_synced.emit(SyncResult("unavailable", message="dns"))
    assert calls == []
    assert _items(d) == [local["project_id"]]
    assert len(spy) == 0
    assert d.delivery_path.text() == "D:/typed/not/saved"
    assert d.central_notice.isHidden()


def test_sync_from_a_background_thread_updates_the_combo_on_the_ui_thread(qtbot, env, monkeypatch):
    bus = EventBus()
    env.pm.create("Alpha local")
    d = _designer(qtbot, env, bus)
    _adopt(env, "Zulu synced", "zzzz")
    ran_on = []
    real = d._fill_combo
    monkeypatch.setattr(d, "_fill_combo",
                        lambda: (ran_on.append(QThread.currentThread()), real())[1])
    emitter = []

    def emit():
        emitter.append(QThread.currentThread())
        bus.central_synced.emit(SyncResult("ok", applied=["zzzz"]))

    t = threading.Thread(target=emit)
    t.start()
    qtbot.waitUntil(lambda: bool(ran_on), timeout=3000)
    t.join(5)
    assert ran_on[0] is d.thread()
    assert ran_on[0] is not emitter[0]
    assert "zzzz" in _items(d)


def test_empty_designer_selects_and_loads_the_first_synced_project(qtbot, env):
    bus = EventBus()
    d = _designer(qtbot, env, bus)
    assert d.project_combo.count() == 0 and d.config is None
    _adopt(env, "Invoices", "aaaa")
    bus.central_synced.emit(SyncResult("ok", applied=["aaaa"]))
    assert d.project_combo.currentData() == "aaaa"
    assert d.config["project_id"] == "aaaa"
    assert not d.managed_banner.isHidden()
    assert d.central_notice.isHidden()


def test_designer_without_a_bus_still_works(qtbot, env):
    env.pm.create("Alpha local")
    d = WorkflowDesigner(env.db, env.pm, ADMIN)
    qtbot.addWidget(d)
    assert d.project_combo.count() == 1


# ---- Item 3: the save button says what it saves ------------------------------------------
def test_save_button_label_follows_managed_state_and_selection(qtbot, env):
    bus = EventBus()
    local = env.pm.create("Alpha local")
    managed = _adopt(env, "Zulu managed", "zzzz")
    d = _designer(qtbot, env, bus)
    d.project_combo.setCurrentIndex(d.project_combo.findData(local["project_id"]))
    assert d.save_btn.text() == "Save (new version)"
    d.project_combo.setCurrentIndex(d.project_combo.findData(managed))
    assert d.save_btn.text() == "Save local settings"
    d.project_combo.setCurrentIndex(d.project_combo.findData(local["project_id"]))
    assert d.save_btn.text() == "Save (new version)"


def test_save_button_label_flips_back_when_the_project_is_unassigned_by_a_sync(qtbot, env):
    bus = EventBus()
    pid = _adopt(env, "Invoices", "aaaa")
    d = _designer(qtbot, env, bus)
    assert d.save_btn.text() == "Save local settings"
    env.db.unmanage_central_project(pid)
    bus.central_synced.emit(SyncResult("ok", unassigned=[pid]))
    assert d.save_btn.text() == "Save (new version)"


# ---- review round 1 -----------------------------------------------------------------------
def test_combo_stays_usable_after_a_successful_sync(qtbot, env):
    bus = EventBus()
    local = env.pm.create("Alpha local")
    d = _designer(qtbot, env, bus)
    other = _adopt(env, "Zulu synced", "zzzz")
    _store_new_delivery_path(env, other, "D:/other/project")
    d.delivery_path.setText("D:/typed/not/saved")
    bus.central_synced.emit(SyncResult("ok", applied=[other]))
    assert not d.project_combo.signalsBlocked()
    assert d.project_combo.currentData() == local["project_id"]
    d.project_combo.setCurrentIndex(d.project_combo.findData(other))
    assert d.config["project_id"] == other
    assert d.delivery_path.text() == "D:/other/project"


def _locked(*a, **k):
    raise sqlite3.OperationalError("database is locked secret-detail")


def test_database_error_during_a_sync_is_logged_and_leaves_the_designer_usable(
        qtbot, env, monkeypatch, caplog):
    bus = EventBus()
    local = env.pm.create("Alpha local")
    other = _adopt(env, "Zulu synced", "zzzz")
    _store_new_delivery_path(env, other, "D:/other/project")
    d = _designer(qtbot, env, bus)
    d.project_combo.setCurrentIndex(d.project_combo.findData(local["project_id"]))
    d.delivery_path.setText("D:/typed/not/saved")
    monkeypatch.setattr(env.db, "list_projects", _locked)
    with caplog.at_level(logging.WARNING, logger="ui.workflow_designer"):
        bus.central_synced.emit(SyncResult("ok", applied=[other]))      # must not raise
    records = [r for r in caplog.records if r.name == "ui.workflow_designer"]
    assert len(records) == 1 and "OperationalError" in records[0].getMessage()
    assert "secret-detail" not in caplog.text
    assert not d.project_combo.signalsBlocked()
    assert d.delivery_path.text() == "D:/typed/not/saved"
    assert d.project_combo.currentData() == local["project_id"]
    monkeypatch.undo()
    d.project_combo.setCurrentIndex(d.project_combo.findData(other))
    assert d.config["project_id"] == other
    assert d.delivery_path.text() == "D:/other/project"


def test_fill_combo_failing_midway_never_leaves_the_combo_blocked(qtbot, env, monkeypatch):
    env.pm.create("Alpha local")
    env.pm.create("Beta local")
    d = _designer(qtbot, env, None)
    calls = []

    def boom(*a, **k):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("addItem failed")

    monkeypatch.setattr(d.project_combo, "addItem", boom)
    with pytest.raises(RuntimeError):
        d._fill_combo()
    assert not d.project_combo.signalsBlocked()


def test_unmanaged_project_that_becomes_managed_by_a_sync_locks_central_fields(qtbot, env):
    bus = EventBus()
    pid = env.pm.create("Alpha local")["project_id"]
    d = _designer(qtbot, env, bus)
    assert all(b.isEnabled() for b in d._central_boxes())
    assert d.save_btn.text() == "Save (new version)"
    d.pass_score.setValue(11)
    env.db.set_central_project(pid, 1)
    bus.central_synced.emit(SyncResult("ok", applied=[pid]))
    assert not any(b.isEnabled() for b in d._central_boxes())
    assert d.pass_score.value() == 11
    assert d.save_btn.text() == "Save local settings"
