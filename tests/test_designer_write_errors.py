
import pytest

import core.profile_manager as profile_manager
from core.profile_manager import ProfileManager
from core.rbac import User
from core.template_manager import TemplateManager
from data.db import Database
from ui.workflow_designer import WorkflowDesigner

ADMIN = User("u1", "root", "admin")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_manager, "REPLACE_RETRY_DELAY", 0.0)
    db = Database(tmp_path / "d.db")
    pm = ProfileManager(tmp_path / "projects", db)
    criticals = []
    monkeypatch.setattr(
        "ui.workflow_designer.QMessageBox.critical",
        staticmethod(lambda *a, **k: criticals.append((a[1], a[2]))))
    monkeypatch.setattr("ui.workflow_designer.QMessageBox.information",
                        staticmethod(lambda *a, **k: None))
    yield type("E", (), dict(db=db, pm=pm, criticals=criticals,
                             root=tmp_path / "projects"))
    db.close()


def _designer(qtbot, env, managed):
    cfg = env.pm.create("P")
    cfg = env.pm.save(cfg)                      # v2, v1 archived
    if managed:
        env.db.set_central_project(cfg["project_id"], 1)
    d = WorkflowDesigner(env.db, env.pm, ADMIN)
    qtbot.addWidget(d)
    return d, cfg["project_id"]


def _break_replace(monkeypatch):
    def locked(src, dst):
        raise PermissionError(r"C:\secret\path\locked.json")
    monkeypatch.setattr(profile_manager.os, "replace", locked)


def _assert_reported(env, snapshot_before, d, config_before):
    assert len(env.criticals) == 1
    title, text = env.criticals[0]
    assert title == "Could not save"
    assert "could not be written" in text and "secret" not in text
    assert snapshot_before == _snapshot(env)
    assert d.config == config_before
    assert list(env.root.rglob("*.tmp")) == []


def _snapshot(env):
    return {str(p): p.read_bytes() for p in env.root.rglob("*.json")}


@pytest.mark.parametrize("managed", [False, True])
def test_save_shows_critical_box_and_does_not_raise_when_write_fails(
        qtbot, env, monkeypatch, managed):
    d, _ = _designer(qtbot, env, managed)
    before, config_before = _snapshot(env), dict(d.config)
    d.dpi.setValue(d.dpi.value() + 50)
    _break_replace(monkeypatch)
    d.save()
    _assert_reported(env, before, d, config_before)
    assert d.dpi.value() == config_before["scanner"]["dpi"] + 50


def test_rollback_shows_critical_box_and_does_not_raise_when_write_fails(
        qtbot, env, monkeypatch):
    d, _ = _designer(qtbot, env, managed=False)
    monkeypatch.setattr("ui.workflow_designer.QInputDialog.getItem",
                        staticmethod(lambda *a, **k: ("1", True)))
    before, config_before = _snapshot(env), dict(d.config)
    _break_replace(monkeypatch)
    d.rollback_dialog()
    _assert_reported(env, before, d, config_before)


def test_clone_shows_critical_box_and_does_not_raise_when_write_fails(
        qtbot, env, monkeypatch):
    d, _ = _designer(qtbot, env, managed=False)
    monkeypatch.setattr("ui.workflow_designer.QInputDialog.getText",
                        staticmethod(lambda *a, **k: ("Copy", True)))
    before, config_before = _snapshot(env), dict(d.config)
    _break_replace(monkeypatch)
    d.clone_project()
    _assert_reported(env, before, d, config_before)
    assert len(env.db.list_projects()) == 1


def test_new_from_template_shows_critical_box_and_does_not_raise_when_write_fails(
        qtbot, env, monkeypatch):
    d, _ = _designer(qtbot, env, managed=False)
    TemplateManager(env.db).save_as_template(d.collect_config(), "T", "Cat")
    monkeypatch.setattr("ui.workflow_designer.QInputDialog.getItem",
                        staticmethod(lambda a, b, c, items, *r, **k:
                                     (items[0], True)))
    monkeypatch.setattr("ui.workflow_designer.QInputDialog.getText",
                        staticmethod(lambda *a, **k: ("From T", True)))
    before, config_before = _snapshot(env), dict(d.config)
    _break_replace(monkeypatch)
    d.new_from_template()
    _assert_reported(env, before, d, config_before)
    assert len(env.db.list_projects()) == 1
