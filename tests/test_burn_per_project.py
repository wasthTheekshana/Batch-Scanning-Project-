"""The CD/DVD tab burns the queue of whichever project is chosen, not only the
project that was open when the application started."""
import sys

import pytest

from core.profile_manager import ProfileManager
from core.rbac import User
from data.db import Database
from delivery.cddvd_channel import CDDVDChannel
from ui.admin_panel import BurnTab

ADMIN = User("u1", "root", "admin")
VIEWER = User("u2", "view", "viewer")


@pytest.fixture()
def env(tmp_path):
    db = Database(tmp_path / "b.db")
    pm = ProfileManager(tmp_path / "projects", db, data_root=tmp_path / "root")
    yield type("E", (), dict(db=db, pm=pm, tmp=tmp_path))
    db.close()


def burner(tmp_path, name):
    script = tmp_path / f"burn_{name}.py"
    disc = tmp_path / f"disc_{name}"
    script.write_text(
        "import shutil, sys, pathlib\n"
        "src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])\n"
        "dst.mkdir(parents=True, exist_ok=True)\n"
        "[shutil.copy2(f, dst / f.name) for f in src.iterdir()"
        " if f.is_file() and f.name != 'manifest.json']\n")
    return disc, {"enabled": True, "staging_dir": str(tmp_path / f"queue_{name}"),
                  "verify_dir": str(disc),
                  "burner_cmd": f'"{sys.executable}" "{script}" "{{source_dir}}" "{disc}"'}


def project(env, name, cd_block):
    cfg = env.pm.create(name)
    cfg["delivery"]["cd_dvd"] = cd_block
    return env.pm.save(cfg)


def stage(block, tmp_path, name):
    doc = tmp_path / f"{name}.pdf"
    doc.write_bytes(b"%PDF-" + name.encode())
    CDDVDChannel(block).deliver(str(doc), {})


def test_every_project_with_cd_dvd_on_is_listed_and_the_others_are_not(qtbot, env):
    _d1, a = burner(env.tmp, "a")
    _d2, b = burner(env.tmp, "b")
    project(env, "Alpha", a)
    project(env, "Beta", b)
    project(env, "Gamma", {"enabled": False})                       # CD/DVD off
    project(env, "Delta", {"enabled": True})                        # on, but no queue or burner set
    tab = BurnTab(None, ADMIN, db=env.db)
    qtbot.addWidget(tab)
    names = [tab.project_combo.itemText(i) for i in range(tab.project_combo.count())]
    assert names == ["Alpha", "Beta"]


def test_each_project_shows_its_own_queue_and_burns_only_that_one(qtbot, env):
    d1, a = burner(env.tmp, "a")
    d2, b = burner(env.tmp, "b")
    project(env, "Alpha", a)
    project(env, "Beta", b)
    stage(a, env.tmp, "one")
    stage(a, env.tmp, "two")
    stage(b, env.tmp, "three")
    tab = BurnTab(None, ADMIN, db=env.db)
    qtbot.addWidget(tab)
    assert "2 file(s)" in tab.queue_label.text()                    # Alpha
    tab.project_combo.setCurrentIndex(1)
    assert "1 file(s)" in tab.queue_label.text()                    # Beta
    tab.label_edit.setText("BETA_DISC")
    tab.burn_now()
    assert "Burn OK" in tab.result.text()
    assert (d2 / "three.pdf").exists() and not d1.exists()          # Beta burned, Alpha untouched
    assert "0 file(s)" in tab.queue_label.text()
    tab.project_combo.setCurrentIndex(0)
    assert "2 file(s)" in tab.queue_label.text()                    # Alpha's queue is still waiting


def test_a_project_whose_settings_change_is_picked_up_on_reload(qtbot, env):
    _d, a = burner(env.tmp, "a")
    cfg = project(env, "Alpha", a)
    tab = BurnTab(None, ADMIN, db=env.db)
    qtbot.addWidget(tab)
    assert tab.project_combo.count() == 1
    cfg["delivery"]["cd_dvd"] = {"enabled": False}
    env.pm.save(cfg)
    tab.reload_projects()
    assert tab.project_combo.count() == 0 and not tab.burn_btn.isEnabled()
    assert "not enabled in any project" in tab.result.text()


def test_the_chosen_project_is_kept_when_the_list_is_reloaded(qtbot, env):
    _d1, a = burner(env.tmp, "a")
    _d2, b = burner(env.tmp, "b")
    project(env, "Alpha", a)
    project(env, "Beta", b)
    tab = BurnTab(None, ADMIN, db=env.db)
    qtbot.addWidget(tab)
    tab.project_combo.setCurrentIndex(1)
    tab.reload_projects()
    assert tab.project_combo.currentText() == "Beta"


def test_the_startup_channel_still_works_when_no_project_lists_cd_dvd(qtbot, env):
    _d, a = burner(env.tmp, "a")
    stage(a, env.tmp, "x")
    tab = BurnTab(CDDVDChannel(a), ADMIN, db=env.db)
    qtbot.addWidget(tab)
    assert tab.project_combo.count() == 1 and "start-up" in tab.project_combo.itemText(0)
    assert "1 file(s)" in tab.queue_label.text() and tab.burn_btn.isEnabled()


def test_only_a_user_who_may_back_up_can_burn(qtbot, env):
    _d, a = burner(env.tmp, "a")
    project(env, "Alpha", a)
    tab = BurnTab(None, VIEWER, db=env.db)
    qtbot.addWidget(tab)
    assert tab.project_combo.count() == 1 and not tab.burn_btn.isEnabled()


def test_a_damaged_project_does_not_break_the_list(qtbot, env):
    _d, a = burner(env.tmp, "a")
    project(env, "Alpha", a)
    bad = env.pm.create("Broken")
    env.db.conn.execute("update projects set config_json='{nope' where project_id=?", (bad["project_id"],))
    env.db.conn.commit()
    tab = BurnTab(None, ADMIN, db=env.db)
    qtbot.addWidget(tab)
    assert [tab.project_combo.itemText(i) for i in range(tab.project_combo.count())] == ["Alpha"]
