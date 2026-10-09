import copy
import inspect
import threading

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

import ui.central_tab
from core.central_client import (CentralRegistrationError, CentralUnavailable,
                                 RegisterResult)
from core.central_state import CentralState
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
    monkeypatch.setattr("ui.central_tab.QMessageBox.warning",
                        staticmethod(lambda *a, **k: shown.append(a[2])))
    env.svc._client_factory = default_client_factory
    t = tab(env, qtbot=qtbot)
    t.url_edit.setText("http://central.example")
    t.code_edit.setText("ABCDEFGH1234")
    t.register()
    assert not env.state.is_registered() and shown and "https" in shown[0].lower()


def test_register_bad_code_shows_message_and_keeps_code(qtbot, env, monkeypatch):
    shown = []
    monkeypatch.setattr("ui.central_tab.QMessageBox.warning",
                        staticmethod(lambda *a, **k: shown.append(a[2])))
    env.holder["client"] = StubClient(exc=CentralRegistrationError("bad"))
    t = tab(env, qtbot=qtbot)
    t.url_edit.setText("https://central.example")
    t.code_edit.setText("ABCDEFGH1234")
    t.register()
    assert not env.state.is_registered() and shown
    assert env.worker.woken == 0
    assert t.code_edit.text() == "ABCDEFGH1234"


def test_register_requires_both_fields(qtbot, env, monkeypatch):
    monkeypatch.setattr("ui.central_tab.QMessageBox.warning",
                        staticmethod(lambda *a, **k: None))
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


def test_status_reports_result_and_restart_hint(qtbot, env):
    env.state.register_result("https://central.example", "pc-1", "k", 600)
    t = tab(env, qtbot=qtbot)
    assert "restart" not in t.status_text(SyncResult("ok", unchanged=1)).lower()
    text = t.status_text(SyncResult("ok", applied=["p1"]))
    assert "restart" in text.lower()
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
    assert "restart" not in t.status_label.text().lower()
    bus.central_synced.emit(SyncResult("ok", applied=["p1"]))
    assert "restart" in t.status_label.text().lower()


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
    monkeypatch.setattr("ui.central_tab.QMessageBox.warning",
                        staticmethod(lambda *a, **k: shown.append(a[2])))
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
    qtbot.waitUntil(lambda: "restart" in t.status_label.text().lower(),
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
    assert "restart" in win.admin_panel.central_tab.status_label.text().lower()


# ---- Workflow Designer guard -------------------------------------------------------------
def test_designer_refuses_to_save_a_centrally_managed_project(qtbot, env, monkeypatch):
    cfg = env.pm.create("Managed")
    env.db.set_central_project(cfg["project_id"], 1)
    d = WorkflowDesigner(env.db, env.pm, ADMIN)
    qtbot.addWidget(d)
    assert d.config["project_id"] == cfg["project_id"]     # designer shows it
    shown = []
    monkeypatch.setattr("ui.workflow_designer.QMessageBox.warning",
                        staticmethod(lambda *a, **k: shown.append(a[2])))
    monkeypatch.setattr("ui.workflow_designer.QMessageBox.information",
                        staticmethod(lambda *a, **k: None))   # never block if unguarded
    before = env.pm.load(cfg["project_id"])["version"]
    d.save()
    assert shown and "central" in shown[0].lower()
    assert env.pm.load(cfg["project_id"])["version"] == before


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
