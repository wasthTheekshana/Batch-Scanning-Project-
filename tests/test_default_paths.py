import copy

import pytest

from projects.profile_manager import DEFAULT_PROJECT, ProfileManager


@pytest.fixture()
def pm(tmp_path):
    return ProfileManager(tmp_path / "projects", data_root=tmp_path / "root")


def test_create_fills_blank_export_and_delivery_paths(pm, tmp_path):
    cfg = pm.create("P")
    assert cfg["export"]["output_path"] == str(tmp_path / "root" / "output")
    assert cfg["delivery"]["local_folder"]["path"] == \
        str(tmp_path / "root" / "delivered")


def test_create_keeps_paths_that_are_already_set(pm):
    base = copy.deepcopy(DEFAULT_PROJECT)
    base["export"]["output_path"] = "X:/mine"
    base["delivery"]["local_folder"]["path"] = "X:/theirs"
    cfg = pm.create("P", config=base)
    assert cfg["export"]["output_path"] == "X:/mine"
    assert cfg["delivery"]["local_folder"]["path"] == "X:/theirs"


def test_disabled_local_delivery_keeps_blank_path(pm):
    base = copy.deepcopy(DEFAULT_PROJECT)
    base["delivery"]["local_folder"]["enabled"] = False
    assert pm.create("P", config=base)["delivery"]["local_folder"]["path"] == ""


def test_save_repairs_a_project_stored_with_blank_paths(pm, tmp_path):
    cfg = pm.create("P")
    cfg["export"]["output_path"] = ""
    cfg["delivery"]["local_folder"]["path"] = ""
    saved = pm.save(cfg)
    assert saved["export"]["output_path"] == str(tmp_path / "root" / "output")
    assert saved["delivery"]["local_folder"]["path"]


def test_without_a_data_root_nothing_is_filled(tmp_path):
    cfg = ProfileManager(tmp_path / "p").create("P")
    assert cfg["export"]["output_path"] == ""
