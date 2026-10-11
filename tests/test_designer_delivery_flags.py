import pytest
from PyQt6.QtWidgets import QLabel

from core.profile_manager import ProfileManager
from core.rbac import User
from data.db import Database
from ui.workflow_designer import WorkflowDesigner

ADMIN = User("u1", "root", "admin")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db = Database(tmp_path / "d.db")
    pm = ProfileManager(tmp_path / "projects", db)
    monkeypatch.setattr("ui.workflow_designer.QMessageBox.information",
                        staticmethod(lambda *a, **k: None))
    yield type("E", (), dict(db=db, pm=pm))
    db.close()


def _make(qtbot, env, managed, sftp=False, cd=False, f_sftp=False, f_cd=False):
    cfg = env.pm.create("P")
    cfg["delivery"]["sftp"]["enabled"] = sftp
    cfg["delivery"]["cd_dvd"]["enabled"] = cd
    cfg["features"]["sftp"] = f_sftp
    cfg["features"]["cd_burn"] = f_cd
    cfg = env.pm.save(cfg)
    if managed:
        env.db.set_central_project(cfg["project_id"], 1)
    d = WorkflowDesigner(env.db, env.pm, ADMIN)
    qtbot.addWidget(d)
    assert d.config["project_id"] == cfg["project_id"]
    return d, cfg["project_id"]


@pytest.mark.parametrize("channel,box_attr", [("sftp", "sftp_enabled"),
                                              ("cd_dvd", "cd_enabled")])
@pytest.mark.parametrize("start,target", [(False, True), (True, False)])
def test_managed_enabled_checkbox_is_saved_without_touching_central_parts(
        qtbot, env, channel, box_attr, start, target):
    kwargs = {"sftp" if channel == "sftp" else "cd": start}
    d, pid = _make(qtbot, env, managed=True, **kwargs)
    before = env.pm.load(pid)
    box = getattr(d, box_attr)
    assert box.isEnabled() and box.isChecked() is start
    box.setChecked(target)
    d.save()
    after = env.pm.load(pid)
    assert after["delivery"][channel]["enabled"] is target
    assert after["version"] == before["version"] + 1
    for key in ("features", "rules", "ocr"):
        assert after[key] == before[key], key


def test_managed_feature_toggles_stay_disabled_and_unsaved(qtbot, env):
    d, pid = _make(qtbot, env, managed=True)
    assert not d.feature_checks["sftp"].isEnabled()
    assert not d.feature_checks["cd_burn"].isEnabled()
    d.sftp_enabled.setChecked(True)
    d.save()
    assert env.pm.load(pid)["features"]["sftp"] is False


def test_unmanaged_feature_toggle_drives_the_checkbox(qtbot, env):
    d, _ = _make(qtbot, env, managed=False)
    d.feature_checks["sftp"].setChecked(True)
    d.feature_checks["cd_burn"].setChecked(True)
    assert d.sftp_enabled.isChecked() and d.cd_enabled.isChecked()
    cfg = d.collect_config()
    assert cfg["delivery"]["sftp"]["enabled"] is True
    assert cfg["delivery"]["cd_dvd"]["enabled"] is True
    d.feature_checks["sftp"].setChecked(False)
    assert not d.sftp_enabled.isChecked()


def test_unmanaged_checkbox_can_diverge_from_toggle_and_is_what_is_saved(
        qtbot, env):
    d, pid = _make(qtbot, env, managed=False)
    d.feature_checks["sftp"].setChecked(True)
    d.sftp_enabled.setChecked(False)
    d.cd_enabled.setChecked(True)          # cd_burn toggle left unticked
    d.save()
    after = env.pm.load(pid)
    assert after["features"]["sftp"] is True
    assert after["delivery"]["sftp"]["enabled"] is False
    assert after["features"]["cd_burn"] is False
    assert after["delivery"]["cd_dvd"]["enabled"] is True


@pytest.mark.parametrize("managed", [True, False])
def test_loading_sets_checkboxes_from_delivery_not_features(
        qtbot, env, managed):
    d, _ = _make(qtbot, env, managed=managed, sftp=True, cd=True,
                 f_sftp=False, f_cd=False)
    assert not d.feature_checks["sftp"].isChecked()
    assert d.sftp_enabled.isChecked()
    assert d.cd_enabled.isChecked()


def test_hints_no_longer_point_at_the_feature_toggle(qtbot, env):
    d, _ = _make(qtbot, env, managed=True)
    for box in (d.sftp_box, d.cd_box):
        text = " ".join(lbl.text() for lbl in box.findChildren(QLabel))
        assert "feature toggle" not in text
        assert "Tick Enabled" in text
