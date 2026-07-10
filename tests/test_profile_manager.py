import copy

import pytest

from core.profile_manager import (DEFAULT_PROJECT, ProfileManager,
                                  ProjectConfigError)


@pytest.fixture()
def pm(tmp_path):
    return ProfileManager(tmp_path / "projects")


def test_default_project_validates(pm):
    cfg = pm.create("Invoices — Client A")
    assert cfg["project_id"] and cfg["version"] == 1
    assert cfg["qc"]["pass_score"] == 80


def test_invalid_config_rejected(pm):
    bad = copy.deepcopy(DEFAULT_PROJECT)
    bad["qc"] = {"enabled": True}  # missing stages / pass_score
    with pytest.raises(ProjectConfigError):
        pm.validate(bad)


def test_save_bumps_version_and_archives(pm):
    cfg = pm.create("P")
    cfg["scanner"]["dpi"] = 600
    saved = pm.save(cfg)
    assert saved["version"] == 2
    assert pm.list_versions(cfg["project_id"]) == [1]
    assert pm.load(cfg["project_id"])["scanner"]["dpi"] == 600


def test_clone_resets_version_and_links_parent(pm):
    parent = pm.create("Parent")
    pm.save({**parent, "scanner": {**parent["scanner"], "dpi": 400}})
    child = pm.clone(parent["project_id"], "Child")
    assert child["version"] == 1
    assert child["cloned_from"] == parent["project_id"]
    assert child["project_id"] != parent["project_id"]
    assert child["scanner"]["dpi"] == 400


def test_rollback_restores_archived_settings(pm):
    cfg = pm.create("P")
    original_dpi = cfg["scanner"]["dpi"]
    cfg["scanner"]["dpi"] = 600
    pm.save(cfg)                                   # v2
    restored = pm.rollback(cfg["project_id"], 1)   # back to v1 content
    assert restored["scanner"]["dpi"] == original_dpi
    assert restored["version"] == 3  # rollback recorded as a new save


def test_rollback_missing_version_raises(pm):
    cfg = pm.create("P")
    with pytest.raises(ProjectConfigError):
        pm.rollback(cfg["project_id"], 99)
