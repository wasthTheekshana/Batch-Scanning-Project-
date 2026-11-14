"""A project whose stored settings are damaged can be put right again."""
import json

import pytest

from projects.profile_manager import ProfileManager
from projects.project_repair import describe, find_intact_copy, repair, unreadable_rows
from projects.project_store import readable_projects
from security.rbac import Session, User
from data.db import Database


@pytest.fixture()
def env(tmp_path):
    db = Database(tmp_path / "r.db")
    pm = ProfileManager(tmp_path / "projects", db, data_root=tmp_path / "root")
    cfg = pm.create("Bank")
    cfg = pm.save({**cfg, "ocr": {**cfg["ocr"], "lang": "eng"}})      # v2; v1 is archived
    yield type("E", (), dict(db=db, pm=pm, cfg=cfg, tmp=tmp_path))
    Session.logout()
    db.close()


def damage_row(db, project_id, text="{broken"):
    db.conn.execute("update projects set config_json=? where project_id=?", (text, project_id))
    db.conn.commit()


def test_a_damaged_row_is_found_and_the_healthy_ones_are_not(env):
    healthy = env.pm.create("Fine")
    damage_row(env.db, env.cfg["project_id"])
    ids = [r["project_id"] for r in unreadable_rows(env.db)]
    assert ids == [env.cfg["project_id"]] and healthy["project_id"] not in ids


def test_the_current_settings_file_is_the_first_choice(env):
    damage_row(env.db, env.cfg["project_id"])
    cfg, where = find_intact_copy(env.pm, env.cfg["project_id"])
    assert where == "the current settings file" and cfg["version"] == env.cfg["version"]
    result = repair(env.pm, env.db, env.cfg["project_id"])
    assert result["source"] == "the current settings file"
    readable, bad = readable_projects(env.db.list_projects("active"))
    assert bad == [] and [r["name"] for r, _c in readable] == ["Bank"]
    assert env.db.get_project(env.cfg["project_id"])["version"] == result["version"] > env.cfg["version"]


def test_an_archived_version_is_used_when_the_file_is_damaged_too(env):
    pid = env.cfg["project_id"]
    env.pm._path(pid).write_text("{not json", encoding="utf-8")
    damage_row(env.db, pid)
    cfg, where = find_intact_copy(env.pm, pid)
    assert where == "saved version 1" and cfg["version"] == 1
    assert repair(env.pm, env.db, pid)["source"] == "saved version 1"
    assert json.loads(env.pm._path(pid).read_text(encoding="utf-8"))["name"] == "Bank"


def test_the_newest_valid_archive_wins_and_invalid_ones_are_skipped(env):
    pid = env.cfg["project_id"]
    cfg3 = env.pm.save({**env.cfg, "ocr": {**env.cfg["ocr"], "lang": "eng"}})     # v3
    env.pm._path(pid).unlink()
    (env.pm.archive_dir / pid / "v2.json").write_text("garbage", encoding="utf-8")
    cfg, where = find_intact_copy(env.pm, pid)
    assert where == "saved version 1"          # v2 is unreadable; v3 was never archived (it was current)
    assert cfg3["version"] == 3


def test_without_any_intact_copy_the_default_needs_an_explicit_yes(env):
    pid = env.cfg["project_id"]
    env.pm._path(pid).unlink()
    for p in (env.pm.archive_dir / pid).glob("*.json"):
        p.unlink()
    damage_row(env.db, pid)
    assert find_intact_copy(env.pm, pid) is None and "default" in describe(env.pm, pid)
    with pytest.raises(ValueError):
        repair(env.pm, env.db, pid)
    result = repair(env.pm, env.db, pid, allow_default=True)
    assert result["source"] == "the default settings" and result["name"] == "Bank"
    readable, bad = readable_projects(env.db.list_projects("active"))
    assert bad == [] and readable[0][1]["project_id"] == pid
    ProfileManager.validate(readable[0][1])


def test_the_damaged_text_is_kept_aside(env):
    pid = env.cfg["project_id"]
    damage_row(env.db, pid, "{precious but broken")
    result = repair(env.pm, env.db, pid)
    kept = result["damaged_copy"]
    assert kept and open(kept, encoding="utf-8").read() == "{precious but broken"


def test_repairing_an_unknown_project_is_refused(env):
    with pytest.raises(LookupError):
        repair(env.pm, env.db, "nope")


# -- the dialog and the window ---------------------------------------------------------------------------
class Audit:
    def __init__(self):
        self.rows = []

    def log(self, action, user, **kw):
        self.rows.append((action, user, kw))


def test_the_dialog_lists_and_repairs_a_damaged_project(qtbot, env):
    from ui.project_repair_dialog import ProjectRepairDialog
    damage_row(env.db, env.cfg["project_id"])
    audit = Audit()
    dlg = ProjectRepairDialog(env.db, env.pm, audit, User("u1", "root", "admin"))
    qtbot.addWidget(dlg)
    assert dlg.list.count() == 1 and "current settings file" in dlg.list.item(0).text()
    with qtbot.waitSignal(dlg.repaired) as sig:
        result = dlg.repair_selected()
    assert sig.args == [env.cfg["project_id"]] and result["source"] == "the current settings file"
    assert audit.rows[0][0] == "project_repaired" and dlg.list.count() == 0
    assert "restored" in dlg.status.text() and not dlg.repair_btn.isEnabled()


def test_the_dialog_asks_before_resetting_to_default(qtbot, env, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from ui.project_repair_dialog import ProjectRepairDialog
    pid = env.cfg["project_id"]
    env.pm._path(pid).unlink()
    for p in (env.pm.archive_dir / pid).glob("*.json"):
        p.unlink()
    damage_row(env.db, pid)
    dlg = ProjectRepairDialog(env.db, env.pm)
    qtbot.addWidget(dlg)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    assert dlg.repair_selected() is None and dlg.list.count() == 1       # declined: untouched
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    assert dlg.repair_selected()["source"] == "the default settings"
    assert dlg.list.count() == 0


def test_the_startup_notice_offers_repair_to_someone_who_may_edit_projects(qtbot, env, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from ui.event_bus import EventBus
    from ui.main_window import MainWindow
    damage_row(env.db, env.cfg["project_id"])
    env.db.insert_user("u-a", "adm", "x", "admin")
    from security.rbac import hash_password
    env.db.conn.execute("update users set password_hash=? where username='adm'", (hash_password("pw"),))
    env.db.conn.commit()
    Session.login("adm", "pw", env.db)
    win = MainWindow(env.db, env.pm, EventBus(), staging_dir=env.tmp / "staging")
    qtbot.addWidget(win)
    opened = []
    monkeypatch.setattr(MainWindow, "open_project_repair", lambda self: opened.append(1))
    labels = []

    def fake_exec(self):
        labels.append([b.text() for b in self.buttons()])
        for b in self.buttons():
            if b.text() == "Repair…":
                self._clicked = b
        return 0
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: getattr(self, "_clicked", None))
    assert win.report_unreadable_projects() == 1
    assert any("Repair…" in names for names in labels) and opened == [1]


def test_a_viewer_is_not_offered_the_repair(qtbot, env, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from ui.event_bus import EventBus
    from security.rbac import hash_password
    from ui.main_window import MainWindow
    damage_row(env.db, env.cfg["project_id"])
    env.db.insert_user("u-v", "view", hash_password("pw"), "viewer")
    Session.login("view", "pw", env.db)
    win = MainWindow(env.db, env.pm, EventBus(), staging_dir=env.tmp / "staging")
    qtbot.addWidget(win)
    labels = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: labels.append([b.text() for b in self.buttons()]))
    win.report_unreadable_projects()
    assert not any("Repair…" in names for names in labels)
