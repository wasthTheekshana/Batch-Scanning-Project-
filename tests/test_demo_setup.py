import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import demo_setup  # noqa: E402


@pytest.fixture()
def base(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "unused"))   # restored by monkeypatch
    return tmp_path / demo_setup.DEMO_NAME


def open_demo(root):
    import main as app_main
    from data.keystore import KeyStore
    return app_main.open_database(root, KeyStore(root / "keys" / "master.bin").get_or_create())


def test_setup_creates_a_login_and_the_bank_project(base):
    root = demo_setup.setup(base)
    assert root == base / "DOKScanner"
    db = open_demo(root)
    try:
        assert db.get_user("admin") is not None
        assert [r["name"] for r in db.list_projects()] == [demo_setup.PROJECT_NAME]
        assert any(t["name"] == demo_setup.PROJECT_NAME for t in db.list_templates())
    finally:
        db.close()


def test_the_demo_project_has_paths_filled_in_under_the_demo_folder(base):
    import json
    root = demo_setup.setup(base)
    db = open_demo(root)
    try:
        cfg = json.loads(db.list_projects()[0]["config_json"])
    finally:
        db.close()
    assert cfg["export"]["output_path"] == str(root / "output")
    assert cfg["delivery"]["local_folder"]["path"] == str(root / "delivered")
    assert cfg["separation"]["drop_blank_pages"] is True


def test_running_setup_twice_does_not_duplicate_anything(base):
    demo_setup.setup(base)
    demo_setup.setup(base)
    db = open_demo(base / "DOKScanner")
    try:
        assert len(db.list_projects()) == 1
    finally:
        db.close()


def test_reset_wipes_the_demo_folder_and_setup_rebuilds_it(base):
    root = demo_setup.setup(base)
    (root / "output").mkdir(exist_ok=True)
    (root / "output" / "old.pdf").write_bytes(b"x")
    demo_setup.reset(base)
    assert not base.exists()
    demo_setup.setup(base)
    assert not (base / "DOKScanner" / "output" / "old.pdf").exists()


def test_reset_refuses_a_folder_that_is_not_the_demo_folder(tmp_path):
    victim = tmp_path / "DOKScanner"
    victim.mkdir()
    (victim / "dokscanner.db").write_bytes(b"real data")
    with pytest.raises(SystemExit, match="refusing"):
        demo_setup.reset(victim)
    assert (victim / "dokscanner.db").exists()


def test_the_real_data_folder_is_never_the_demo_folder(monkeypatch):
    monkeypatch.setenv("USERPROFILE", "C:/Users/someone")
    assert demo_setup.demo_base().name == "DOKScannerDemo"
