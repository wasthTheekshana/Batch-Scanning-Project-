import copy
import inspect
import logging
import threading

import pytest
from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QListWidgetItem, QMessageBox

import ui.central_tab
import ui.main_window
from core.central_client import (CentralRegistrationError, CentralUnavailable,
                                 RegisterResult)
from core.central_state import CentralState
import core.central_sync
from core.central_sync import (CentralSyncService, SyncResult,
                               default_client_factory)
from core.event_bus import EventBus
from core.profile_manager import DEFAULT_PROJECT, ProfileManager
from core.rbac import Session, User
from data.db import Database
from ui.admin_panel import AdminPanel
from ui.central_tab import CentralTab
from ui.main_window import MainWindow
from ui.workflow_designer import WorkflowDesigner

ADMIN = User("u1", "root", "admin")
VIEWER = User("u2", "view", "viewer")
OPERATOR = User("u3", "op", "operator")


class StubClient:
    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.codes = result, exc, []

    def register(self, code):
        self.codes.append(code)
        if self.exc:
            raise self.exc
        return self.result


class StubWorker:
    def __init__(self):
        self.woken = 0

    def sync_now(self):
        self.woken += 1


@pytest.fixture()
def env(tmp_path):
    db = Database(tmp_path / "ui.db")
    pm = ProfileManager(tmp_path / "projects", db)
    state = CentralState(tmp_path)
    holder = {"client": StubClient(RegisterResult("pc-1", "dsk_K", 600))}
    svc = CentralSyncService(db, pm, state, tmp_path,
                             client_factory=lambda url, t: holder["client"])
    worker = StubWorker()
    yield type("E", (), dict(db=db, pm=pm, state=state, svc=svc,
                             worker=worker, holder=holder, root=tmp_path))
    Session.logout()
    db.close()


def tab(env, user=ADMIN, qtbot=None):
    t = CentralTab(env.state, env.svc, env.worker, user)
    t._run_in_background = lambda fn: fn()      # deterministic: run inline
    if qtbot:
        qtbot.addWidget(t)
    return t


def test_unregistered_status_and_gating(qtbot, env):
    t = tab(env, qtbot=qtbot)
    assert "not registered" in t.status_text().lower()
    assert t.register_btn.isEnabled() and not t.sync_btn.isEnabled()
    assert not t.disconnect_btn.isEnabled()
    v = tab(env, VIEWER, qtbot)
    assert not v.register_btn.isEnabled() and not v.disconnect_btn.isEnabled()


def test_viewer_cannot_use_buttons_even_when_registered(qtbot, env):
    env.state.register_result("https://central.example", "pc-1", "k", 600)
    v = tab(env, VIEWER, qtbot)
    assert not v.register_btn.isEnabled()
    assert not v.sync_btn.isEnabled()
    assert not v.disconnect_btn.isEnabled()
    a = tab(env, ADMIN, qtbot)
    assert a.sync_btn.isEnabled() and a.disconnect_btn.isEnabled()


def test_register_success_updates_state_and_wakes_worker(qtbot, env):
    t = tab(env, qtbot=qtbot)
    t.url_edit.setText("https://central.example")
    t.code_edit.setText("ABCDEFGH1234")
    t.register()
    assert env.state.is_registered() and env.worker.woken == 1
    assert t.code_edit.text() == ""                       # one-time code is cleared
    assert t.sync_btn.isEnabled() and t.disconnect_btn.isEnabled()
    assert "pc-1" in t.status_text()


def test_register_rejects_http_url_with_message(qtbot, env, monkeypatch):
    shown = []
    monkeypatch.setattr(CentralTab, "_show_warning",
                        lambda self, text: shown.append(text))
    env.svc._client_factory = default_client_factory
    t = tab(env, qtbot=qtbot)
    t.url_edit.setText("http://central.example")
    t.code_edit.setText("ABCDEFGH1234")
    t.register()
    assert not env.state.is_registered() and shown and "https" in shown[0].lower()


def test_register_bad_code_shows_message_and_keeps_code(qtbot, env, monkeypatch):
    shown = []
    monkeypatch.setattr(CentralTab, "_show_warning",
                        lambda self, text: shown.append(text))
    env.holder["client"] = StubClient(exc=CentralRegistrationError("bad"))
    t = tab(env, qtbot=qtbot)
    t.url_edit.setText("https://central.example")
    t.code_edit.setText("ABCDEFGH1234")
    t.register()
    assert not env.state.is_registered() and shown
    assert env.worker.woken == 0
    assert t.code_edit.text() == "ABCDEFGH1234"


def test_register_requires_both_fields(qtbot, env, monkeypatch):
    monkeypatch.setattr(CentralTab, "_show_warning", lambda self, text: None)
    t = tab(env, qtbot=qtbot)
    t.url_edit.setText("https://central.example")
    t.register()
    assert env.holder["client"].codes == []


def test_auth_failed_banner_and_reenroll(qtbot, env):
    env.state.register_result("https://central.example", "pc-1", "k", 600)
    env.state.update(auth_failed=True)
    t = tab(env, qtbot=qtbot)
    assert "enrollment code" in t.status_text().lower()
    assert t.register_btn.isEnabled()                      # can re-enroll while registered


APPLIED_WORDING = ("Updated project settings were received; "
                   "they apply to the next scan batch.")


def test_status_reports_result_and_applied_hint(qtbot, env):
    env.state.register_result("https://central.example", "pc-1", "k", 600)
    t = tab(env, qtbot=qtbot)
    assert "next scan batch" not in t.status_text(SyncResult("ok", unchanged=1))
    text = t.status_text(SyncResult("ok", applied=["p1"]))
    assert APPLIED_WORDING in text
    assert "restart" not in text.lower()
    assert "unavailable" in t.status_text(SyncResult("unavailable", message="dns")).lower()
    assert "skipped" in t.status_text(SyncResult("ok", errors={"p2": "bad"})).lower()


def test_projects_table_lists_managed_state(qtbot, env):
    cfg = copy.deepcopy(DEFAULT_PROJECT)
    cfg.update(project_id="aaaa", name="Invoices",
               created="2026-10-09T00:00:00+00:00")
    cfg["rules"]["rule_set_id"] = "r"
    env.pm.adopt(cfg)
    env.db.set_central_project("aaaa", 3)
    t = tab(env, qtbot=qtbot)
    t.db = env.db
    t.refresh_status()
    assert t.projects_table.rowCount() == 1
    assert t.projects_table.item(0, 0).text() == "Invoices"
    assert "3" in t.projects_table.item(0, 1).text()


def test_projects_table_headers_are_fully_visible(qtbot, env):
    t = tab(env, qtbot=qtbot)
    t.show()
    header = t.projects_table.horizontalHeader()
    metrics = QFontMetrics(header.font())
    for i in range(t.projects_table.columnCount()):
        text = t.projects_table.horizontalHeaderItem(i).text()
        assert header.sectionSize(i) >= metrics.horizontalAdvance(text), text


def test_disconnect_asks_for_confirmation(qtbot, env, monkeypatch):
    env.state.register_result("https://central.example", "pc-1", "k", 600)
    t = tab(env, qtbot=qtbot)
    monkeypatch.setattr("ui.central_tab.QMessageBox.question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    t.disconnect()
    assert env.state.is_registered()
    monkeypatch.setattr("ui.central_tab.QMessageBox.question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    t.disconnect()
    assert not env.state.is_registered()


def test_bus_signal_refreshes_status(qtbot, env):
    bus = EventBus()
    env.state.register_result("https://central.example", "pc-1", "k", 600)
    t = CentralTab(env.state, env.svc, env.worker, ADMIN, bus=bus)
    qtbot.addWidget(t)
    assert "next scan batch" not in t.status_label.text()
    bus.central_synced.emit(SyncResult("ok", applied=["p1"]))
    assert APPLIED_WORDING in t.status_label.text()
    assert "restart" not in t.status_label.text().lower()


# ---- Registration runs off the UI thread -------------------------------------------------
class BlockingClient:
    def __init__(self):
        self.release = threading.Event()
        self.thread = None

    def register(self, code):
        self.thread = threading.current_thread()
        self.release.wait(5)
        return RegisterResult("pc-1", "dsk_K", 600)


def test_register_runs_off_the_ui_thread_and_shows_progress(qtbot, env):
    client = BlockingClient()
    env.holder["client"] = client
    t = CentralTab(env.state, env.svc, env.worker, ADMIN)    # real background seam
    qtbot.addWidget(t)
    t.url_edit.setText("https://central.example")
    t.code_edit.setText("ABCDEFGH1234")
    t.register()                                           # must return while blocked
    assert not client.release.is_set()
    assert not t.register_btn.isEnabled()
    assert "registering" in t.status_label.text().lower()
    qtbot.waitUntil(lambda: client.thread is not None, timeout=3000)
    assert client.thread is not threading.main_thread()
    client.release.set()
    qtbot.waitUntil(env.state.is_registered, timeout=3000)
    qtbot.waitUntil(t.register_btn.isEnabled, timeout=3000)
    assert "registering" not in t.status_label.text().lower()
    assert env.worker.woken == 1 and t.code_edit.text() == ""


def test_second_click_while_registering_does_nothing(qtbot, env):
    t = tab(env, qtbot=qtbot)
    pending = []
    t._run_in_background = pending.append                   # hold the work
    t.url_edit.setText("https://central.example")
    t.code_edit.setText("ABCDEFGH1234")
    t.register()
    t.register()
    assert len(pending) == 1 and not t.register_btn.isEnabled()
    pending[0]()
    assert env.holder["client"].codes == ["ABCDEFGH1234"]
    assert t.register_btn.isEnabled() and env.state.is_registered()


def test_register_unavailable_shows_generic_message_without_raw_error(
        qtbot, env, monkeypatch):
    shown = []
    monkeypatch.setattr(CentralTab, "_show_warning",
                        lambda self, text: shown.append(text))
    env.holder["client"] = StubClient(
        exc=CentralUnavailable("URLError: internal-host.corp:8443 refused"))
    t = tab(env, qtbot=qtbot)
    t.url_edit.setText("https://central.example")
    t.code_edit.setText("ABCDEFGH1234")
    t.register()
    assert len(shown) == 1 and "could not reach" in shown[0].lower()
    assert "internal-host" not in shown[0]
    assert not env.state.is_registered() and t.register_btn.isEnabled()
    assert t.code_edit.text() == "ABCDEFGH1234"


# ---- Cross-thread bus delivery -----------------------------------------------------------
def test_central_synced_from_another_thread_updates_on_the_ui_thread(qtbot, env):
    bus = EventBus()
    env.state.register_result("https://central.example", "pc-1", "k", 600)
    t = CentralTab(env.state, env.svc, env.worker, ADMIN, bus=bus)
    qtbot.addWidget(t)
    seen = []
    real = t.refresh_status

    def recording(result=None):
        seen.append(threading.current_thread())
        real(result)
    t.refresh_status = recording
    emitter = threading.Thread(
        target=lambda: bus.central_synced.emit(SyncResult("ok", applied=["p1"])))
    emitter.start()
    qtbot.waitUntil(lambda: APPLIED_WORDING in t.status_label.text(),
                    timeout=3000)
    emitter.join()
    assert seen == [threading.main_thread()]


# ---- Untrusted text ----------------------------------------------------------------------
def test_server_text_is_plain_and_capped(qtbot, env):
    env.state.register_result("https://central.example", "pc-1", "k", 600)
    t = tab(env, qtbot=qtbot)
    result = SyncResult("ok", errors={"<i>p1</i>": "<b>bold</b>" + "x" * 5000})
    t.refresh_status(result)
    text = t.status_label.text()
    assert t.status_label.textFormat() == Qt.TextFormat.PlainText
    assert "<b>bold</b>" in text and "<i>p1</i>" in text      # shown literally
    assert text.count("x") <= ui.central_tab.MAX_TEXT
    assert len(text) < 1000


def test_many_errors_are_summarised(qtbot, env):
    env.state.register_result("https://central.example", "pc-1", "k", 600)
    t = tab(env, qtbot=qtbot)
    errors = {f"p{i}": "bad" for i in range(40)}
    text = t.status_text(SyncResult("ok", errors=errors))
    assert "40 project(s)" in text and "and 35 more" in text
    assert "p39" not in text


def test_control_characters_in_server_text_are_neutralised(qtbot, env):
    env.state.register_result("https://central.example", "pc-1", "k", 600)
    t = tab(env, qtbot=qtbot)
    text = t.status_text(
        SyncResult("unavailable", message="a\x00b\r\nc\x1b[31m"))
    assert not any(ord(c) < 32 for c in text.replace("\n", ""))
    assert "a b" in text


def test_table_shows_project_name_as_plain_text(qtbot, env):
    cfg = copy.deepcopy(DEFAULT_PROJECT)
    cfg.update(project_id="bbbb", name="<b>Evil</b>" + "n" * 500,
               created="2026-10-09T00:00:00+00:00")
    cfg["rules"]["rule_set_id"] = "r"
    env.pm.adopt(cfg)
    env.db.set_central_project("bbbb", 1)
    t = tab(env, qtbot=qtbot)
    cell = t.projects_table.item(0, 0)
    assert cell.text().startswith("<b>Evil</b>")
    assert len(cell.text()) <= ui.central_tab.MAX_TEXT + 1


# ---- API key never surfaces --------------------------------------------------------------
def test_api_key_is_never_read_or_displayed(qtbot, env):
    assert "api_key" not in inspect.getsource(ui.central_tab)
    t = tab(env, qtbot=qtbot)
    t.url_edit.setText("https://central.example")
    t.code_edit.setText("ABCDEFGH1234")
    t.register()
    assert env.state.is_registered()
    shown = [t.status_label.text(), t.url_edit.text(), t.code_edit.text(),
             t.status_text()]
    shown += [t.projects_table.item(r, c).text()
              for r in range(t.projects_table.rowCount()) for c in range(3)]
    assert not any("dsk_K" in x for x in shown)


# ---- AdminPanel / MainWindow hosting -----------------------------------------------------
def test_admin_panel_has_central_tab_only_when_wired(qtbot, env):
    plain = AdminPanel(env.db, ADMIN)
    qtbot.addWidget(plain)
    assert not hasattr(plain, "central_tab")
    wired = AdminPanel(env.db, ADMIN, central_state=env.state,
                       central_service=env.svc, central_worker=env.worker)
    qtbot.addWidget(wired)
    assert isinstance(wired.central_tab, CentralTab)


def _admin_window(qtbot, env, **kw):
    Session._current = ADMIN
    win = MainWindow(env.db, env.pm, EventBus(),
                     staging_dir=env.root / "staging", **kw)
    qtbot.addWidget(win)
    return win


def test_main_window_has_central_tab_only_when_wired(qtbot, env):
    env.pm.create("Default Project")
    plain = _admin_window(qtbot, env)
    assert not hasattr(plain.admin_panel, "central_tab")
    wired = _admin_window(qtbot, env, central_state=env.state,
                          sync_service=env.svc, sync_worker=env.worker)
    ct = wired.admin_panel.central_tab
    assert isinstance(ct, CentralTab)
    assert ct.service is env.svc and ct.worker is env.worker
    assert ct.state is env.state


def test_main_window_forwards_bus_to_central_tab(qtbot, env):
    env.pm.create("Default Project")
    env.state.register_result("https://central.example", "pc-1", "k", 600)
    win = _admin_window(qtbot, env, central_state=env.state,
                        sync_service=env.svc, sync_worker=env.worker)
    win.bus.central_synced.emit(SyncResult("ok", applied=["p1"]))
    assert APPLIED_WORDING in win.admin_panel.central_tab.status_label.text()


def test_main_window_designer_follows_projects_arriving_from_a_sync(qtbot, env):
    env.pm.create("Default Project")
    win = _admin_window(qtbot, env)
    cfg = copy.deepcopy(DEFAULT_PROJECT)
    cfg.update(project_id="zzzz", name="Zulu synced",
               created="2026-10-09T00:00:00+00:00")
    cfg["rules"]["rule_set_id"] = "r"
    env.pm.adopt(cfg)
    win.bus.central_synced.emit(SyncResult("ok", applied=["zzzz"]))
    assert win.designer.project_combo.findData("zzzz") >= 0


# ---- Workflow Designer guard -------------------------------------------------------------
def _designer(qtbot, env, name="Managed", managed=True):
    cfg = env.pm.create(name)
    cfg["delivery"]["sftp"] = {"enabled": True, "host": "h", "username": "u"}
    cfg = env.pm.save(cfg)
    if managed:
        env.db.set_central_project(cfg["project_id"], 1)
    d = WorkflowDesigner(env.db, env.pm, ADMIN)
    qtbot.addWidget(d)
    assert d.config["project_id"] == cfg["project_id"]
    return d, cfg["project_id"]


def _silence_boxes(monkeypatch):
    shown = []
    monkeypatch.setattr("ui.workflow_designer.QMessageBox.warning",
                        staticmethod(lambda *a, **k: shown.append(a[2])))
    monkeypatch.setattr("ui.workflow_designer.QMessageBox.information",
                        staticmethod(lambda *a, **k: shown.append(a[2])))
    return shown


def _edit_everything(d):
    """Edit one local field and several central-owned ones in the form."""
    d.delivery_path.setText("D:/bank/delivered")
    d.pass_score.setValue(11)
    d.feature_checks["duplicate_check"].setChecked(
        not d.feature_checks["duplicate_check"].isChecked())
    d.ocr_mode.setCurrentIndex(1)
    d.fixed_pages.setValue(7)
    d.rule_list.addItem(QListWidgetItem("r001 - sneaky"))
    d.rule_list.item(0).setData(Qt.ItemDataRole.UserRole, {
        "id": "r001", "name": "sneaky",
        "condition": {"field": "page_count", "op": "gt", "value": 1},
        "actions": [{"type": "add_flag", "value": "x"}]})


def test_managed_designer_save_persists_only_local_sections(qtbot, env, monkeypatch):
    d, pid = _designer(qtbot, env)
    shown = _silence_boxes(monkeypatch)
    before = env.pm.load(pid)
    _edit_everything(d)
    d.save()
    after = env.pm.load(pid)
    assert after["delivery"]["local_folder"]["path"] == "D:/bank/delivered"
    assert after["version"] == before["version"] + 1
    for key in before:
        if key in ("scanner", "export", "delivery", "version"):
            continue
        assert after[key] == before[key], key       # rules/ocr/qc/features/... untouched
    assert after["rules"]["rules"] == [] and "mode" not in after["ocr"]
    assert shown and "scanner / export / delivery" in shown[-1]
    assert "managed centrally" in shown[-1].lower()


def test_managed_designer_save_keeps_stored_delivery_flags(qtbot, env, monkeypatch):
    d, pid = _designer(qtbot, env)
    _silence_boxes(monkeypatch)
    d.sftp_host.setText("sftp.bank.lk")
    d.save()
    after = env.pm.load(pid)
    # features.sftp (central) is False, but this PC's delivery.sftp stays on
    assert after["features"]["sftp"] is False
    assert after["delivery"]["sftp"]["enabled"] is True
    assert after["delivery"]["sftp"]["host"] == "sftp.bank.lk"


def test_designer_saves_everything_for_unmanaged_projects(qtbot, env, monkeypatch):
    d, pid = _designer(qtbot, env, managed=False)
    _silence_boxes(monkeypatch)
    _edit_everything(d)
    d.save()
    after = env.pm.load(pid)
    assert after["delivery"]["local_folder"]["path"] == "D:/bank/delivered"
    assert after["qc"]["pass_score"] == 11 and after["separation"]["fixed_pages"] == 7
    assert after["ocr"]["mode"] == "full_page"
    assert [r["id"] for r in after["rules"]["rules"]] == ["r001"]


def test_designer_banner_and_disabled_state_follow_the_selected_project(qtbot, env):
    local = env.pm.create("Alpha local")
    managed = env.pm.create("Zulu managed")
    env.db.set_central_project(managed["project_id"], 1)
    d = WorkflowDesigner(env.db, env.pm, ADMIN)
    qtbot.addWidget(d)
    d.project_combo.setCurrentIndex(d.project_combo.findData(local["project_id"]))
    assert d.managed_banner.isHidden()
    assert all(b.isEnabled() for b in d._central_boxes())
    assert d.exp_box.isEnabled() and d.sftp_box.isEnabled()
    d.project_combo.setCurrentIndex(d.project_combo.findData(managed["project_id"]))
    assert not d.managed_banner.isHidden()
    assert "only scanner / export / delivery" in d.managed_banner.text()
    assert not any(b.isEnabled() for b in d._central_boxes())
    assert d.exp_box.isEnabled() and d.sftp_box.isEnabled()
    env.db.unmanage_central_project(managed["project_id"])      # evaluated live, not cached
    d.project_combo.setCurrentIndex(d.project_combo.findData(local["project_id"]))
    d.project_combo.setCurrentIndex(d.project_combo.findData(managed["project_id"]))
    assert d.managed_banner.isHidden() and all(b.isEnabled() for b in d._central_boxes())


def test_designer_checks_managed_at_click_time_not_at_construction(qtbot, env, monkeypatch):
    d, pid = _designer(qtbot, env, managed=False)
    _silence_boxes(monkeypatch)
    env.db.set_central_project(pid, 1)                          # assigned after the form opened
    _edit_everything(d)
    d.save()
    after = env.pm.load(pid)
    assert after["rules"]["rules"] == [] and after["qc"]["pass_score"] != 11
    assert after["delivery"]["local_folder"]["path"] == "D:/bank/delivered"


def test_designer_still_saves_unmanaged_and_unassigned_projects(qtbot, env, monkeypatch):
    cfg = env.pm.create("Local")
    env.db.set_central_project(cfg["project_id"], 1, managed=False)   # unassigned
    d = WorkflowDesigner(env.db, env.pm, ADMIN)
    qtbot.addWidget(d)
    assert d.config["project_id"] == cfg["project_id"]
    monkeypatch.setattr("ui.workflow_designer.QMessageBox.information",
                        staticmethod(lambda *a, **k: None))
    before = env.pm.load(cfg["project_id"])["version"]
    d.save()
    assert env.pm.load(cfg["project_id"])["version"] == before + 1


def test_designer_refuses_rollback_of_a_managed_project(qtbot, env, monkeypatch):
    cfg = env.pm.create("Managed")
    env.db.set_central_project(cfg["project_id"], 1)
    d = WorkflowDesigner(env.db, env.pm, ADMIN)
    qtbot.addWidget(d)
    shown = []
    monkeypatch.setattr("ui.workflow_designer.QMessageBox.warning",
                        staticmethod(lambda *a, **k: shown.append(a[2])))
    monkeypatch.setattr("ui.workflow_designer.QMessageBox.information",
                        staticmethod(lambda *a, **k: None))
    rolled = []
    monkeypatch.setattr(env.pm, "rollback", lambda *a: rolled.append(a))
    d.rollback_dialog()
    assert shown and "central" in shown[0].lower() and rolled == []


# ---- Updates apply to the next scan batch: nothing is pending, nothing is sticky ----------
def test_applied_result_says_next_scan_batch_and_never_mentions_restart(qtbot, env):
    bus = EventBus()
    env.state.register_result("https://central.example", "pc-1", "k", 600)
    t = CentralTab(env.state, env.svc, env.worker, ADMIN, bus=bus)
    qtbot.addWidget(t)
    bus.central_synced.emit(SyncResult("ok", applied=["p1"]))
    assert APPLIED_WORDING in t.status_label.text()
    assert "restart" not in t.status_label.text().lower()


def test_applied_message_follows_the_latest_result_and_is_not_sticky(qtbot, env):
    bus = EventBus()
    env.state.register_result("https://central.example", "pc-1", "k", 600)
    t = CentralTab(env.state, env.svc, env.worker, ADMIN, bus=bus)
    qtbot.addWidget(t)
    bus.central_synced.emit(SyncResult("ok", applied=["p1"]))
    assert APPLIED_WORDING in t.status_label.text()
    bus.central_synced.emit(SyncResult("ok", unchanged=1))      # the next poll: nothing applied
    assert "next scan batch" not in t.status_label.text()


def test_unregistered_tab_does_not_keep_the_applied_message(qtbot, env):
    bus = EventBus()
    env.state.register_result("https://central.example", "pc-1", "k", 600)
    t = CentralTab(env.state, env.svc, env.worker, ADMIN, bus=bus)
    qtbot.addWidget(t)
    bus.central_synced.emit(SyncResult("ok", applied=["p1"]))
    env.state.clear()
    t.refresh_status(SyncResult("not_registered"))
    assert "next scan batch" not in t.status_label.text()


def test_restart_state_is_gone_from_the_sync_module():
    assert not hasattr(SyncResult("ok"), "restart_needed")
    assert not hasattr(SyncResult("ok", applied=["p1"]), "restart_needed")
    for name in ("restart_pending", "_reset_restart_pending", "note_sync_result"):
        assert not hasattr(core.central_sync, name), name
    assert not hasattr(ui.central_tab, "RESTART_TEXT")
    assert not hasattr(ui.main_window, "RESTART_NOTICE")


def _wired_window(qtbot, env, user):
    env.pm.create("Default Project")
    env.state.register_result("https://central.example", "pc-1", "k", 600)
    Session._current = user
    win = MainWindow(env.db, env.pm, EventBus(), staging_dir=env.root / "staging",
                     central_state=env.state, sync_service=env.svc,
                     sync_worker=env.worker)
    qtbot.addWidget(win)
    return win


def _bar_texts(win):
    from PyQt6.QtWidgets import QLabel
    return [lbl.text() for lbl in win.statusBar().findChildren(QLabel)
            if not lbl.isHidden() and lbl.text()]


@pytest.mark.parametrize("user", [ADMIN, OPERATOR], ids=["admin", "operator"])
def test_main_window_has_no_restart_label_after_an_applied_sync(qtbot, env, user):
    win = _wired_window(qtbot, env, user)
    win.show()
    assert not hasattr(win, "restart_label")
    win.bus.central_synced.emit(SyncResult("ok", applied=["p1"]))
    assert not any("restart" in t.lower() for t in _bar_texts(win))
    assert win.reenroll_label.isHidden()
    env.state.update(auth_failed=True)
    win.bus.central_synced.emit(SyncResult("auth_failed"))
    assert "Central: new enrollment code needed" in _bar_texts(win)
    assert not any("restart" in t.lower() for t in _bar_texts(win))


@pytest.mark.parametrize("user", [ADMIN, OPERATOR], ids=["admin", "operator"])
def test_main_window_shows_reenrollment_notice_only_after_auth_failure(qtbot, env, user):
    env.pm.create("Default Project")
    env.state.register_result("https://central.example", "pc-1", "k", 600)
    Session._current = user
    win = MainWindow(env.db, env.pm, EventBus(), staging_dir=env.root / "staging",
                     central_state=env.state, sync_service=env.svc,
                     sync_worker=env.worker)
    qtbot.addWidget(win)
    win.bus.central_synced.emit(SyncResult("ok"))
    assert win.reenroll_label.isHidden()
    env.state.update(auth_failed=True)
    win.bus.central_synced.emit(SyncResult("auth_failed"))
    assert not win.reenroll_label.isHidden()
    assert "new enrollment code" in win.reenroll_label.text()
    env.state.update(auth_failed=False)                         # re-enrolled
    win.bus.central_synced.emit(SyncResult("ok"))
    assert win.reenroll_label.isHidden()


def test_reenrollment_notice_stays_short_enough_for_the_status_bar(qtbot, env):
    env.pm.create("Default Project")
    env.state.register_result("https://central.example", "pc-1", "k", 600)
    env.state.update(auth_failed=True)
    Session._current = ADMIN
    win = MainWindow(env.db, env.pm, EventBus(), staging_dir=env.root / "staging",
                     central_state=env.state, sync_service=env.svc,
                     sync_worker=env.worker)
    qtbot.addWidget(win)
    win.show()
    win.bus.central_synced.emit(SyncResult("auth_failed", applied=["p1"]))
    assert not win.reenroll_label.isHidden()
    assert win.statusBar().currentMessage().startswith("Signed in as")
    assert len(win.reenroll_label.text()) <= 70


def test_main_window_without_central_objects_has_no_notices(qtbot, env):
    env.pm.create("Default Project")
    win = _admin_window(qtbot, env)
    win.bus.central_synced.emit(SyncResult("ok", applied=["p1"]))
    assert win.reenroll_label.isHidden()


# ---- I4: an unexpected registration failure is logged (type only) ------------------------
def test_unexpected_registration_failure_is_logged_without_code_or_message(
        qtbot, env, monkeypatch, caplog):
    shown = []
    monkeypatch.setattr(CentralTab, "_show_warning",
                        lambda self, text: shown.append(text))
    env.holder["client"] = StubClient(
        exc=RuntimeError("leaky ABCDEFGH1234 internal detail"))
    t = tab(env, qtbot=qtbot)
    t.url_edit.setText("https://central.example")
    t.code_edit.setText("ABCDEFGH1234")
    with caplog.at_level(logging.WARNING, logger="ui.central_tab"):
        t.register()
    records = [r for r in caplog.records if r.name == "ui.central_tab"]
    assert len(records) == 1 and "RuntimeError" in records[0].getMessage()
    assert "ABCDEFGH1234" not in caplog.text and "leaky" not in caplog.text
    assert shown and "log" in shown[0].lower()


# ---- M1: a tab destroyed mid-registration must not break the thread ----------------------
def test_registration_thread_survives_the_tab_being_destroyed(qtbot, env, monkeypatch):
    client = BlockingClient()
    env.holder["client"] = client
    escaped = []
    monkeypatch.setattr(threading, "excepthook", lambda args: escaped.append(args))
    t = CentralTab(env.state, env.svc, env.worker, ADMIN)
    t.url_edit.setText("https://central.example")
    t.code_edit.setText("ABCDEFGH1234")
    t.register()
    qtbot.waitUntil(lambda: client.thread is not None, timeout=3000)
    sip.delete(t)                                            # tab gone, register still blocked
    client.release.set()
    client.thread.join(5)
    assert not client.thread.is_alive()
    assert escaped == []
    assert env.state.is_registered()                         # the registration itself completed


# ---- M2: programmatic calls respect the role gate ----------------------------------------
def test_viewer_cannot_bypass_the_role_gate_by_calling_methods(qtbot, env, monkeypatch):
    env.state.register_result("https://central.example", "pc-1", "k", 600)
    monkeypatch.setattr("ui.central_tab.QMessageBox.question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    v = tab(env, VIEWER, qtbot)
    v.url_edit.setText("https://central.example")
    v.code_edit.setText("ABCDEFGH1234")
    v.register()
    v.sync_now()
    v.disconnect()
    assert env.holder["client"].codes == [] and env.worker.woken == 0
    assert env.state.is_registered()


def test_sync_now_without_a_worker_does_nothing(qtbot, env):
    t = CentralTab(env.state, env.svc, None, ADMIN)
    qtbot.addWidget(t)
    t.sync_now()                                             # must not raise


# ---- M3: warnings are plain text ---------------------------------------------------------
def test_warning_box_renders_plain_text(qtbot, env, monkeypatch):
    boxes = []
    monkeypatch.setattr("ui.central_tab.QMessageBox.exec",
                        lambda self: boxes.append(self) or 0)
    env.holder["client"] = StubClient(exc=CentralRegistrationError("<b>bad</b> code"))
    t = tab(env, qtbot=qtbot)
    t.url_edit.setText("https://central.example")
    t.code_edit.setText("ABCDEFGH1234")
    t.register()
    assert len(boxes) == 1
    assert boxes[0].textFormat() == Qt.TextFormat.PlainText
    assert boxes[0].text() == "<b>bad</b> code"
