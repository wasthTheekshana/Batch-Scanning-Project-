import json
import os
import threading

import pytest

import core.profile_manager as profile_manager
from core.profile_manager import ProfileManager

REAL_REPLACE = os.replace


@pytest.fixture()
def pm(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_manager, "REPLACE_RETRY_DELAY", 0.0)
    return ProfileManager(tmp_path / "projects")




def test_two_threads_writing_one_project_never_raise_and_file_stays_valid(pm):
    base = pm.create("P")
    path = pm._path(base["project_id"])
    cfg_a = {**base, "name": "A" * 20000}
    cfg_b = {**base, "name": "B" * 20000}
    barrier = threading.Barrier(2)
    errors = []

    def writer(cfg):
        for _ in range(50):
            try:
                barrier.wait(timeout=10)
                pm._write(cfg)
            except Exception as e:      # noqa: BLE001 - collect for the assert
                errors.append(repr(e))

    threads = [threading.Thread(target=writer, args=(c,))
               for c in (cfg_a, cfg_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert errors == []
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["name"] in (cfg_a["name"], cfg_b["name"])
    assert list(path.parent.glob("*.tmp")) == []


def test_every_write_uses_a_fresh_unique_temp_file(pm, monkeypatch):
    base = pm.create("P")
    sources = []

    def spy(src, dst):
        sources.append(os.path.basename(src))
        return REAL_REPLACE(src, dst)

    monkeypatch.setattr(profile_manager.os, "replace", spy)
    for _ in range(3):
        pm._write(base)
    assert len(sources) == 3 and len(set(sources)) == 3
    assert f"{base['project_id']}.json.tmp" not in sources
    assert all(s.endswith(".tmp") and s.startswith(base["project_id"])
               for s in sources)


def test_permission_error_twice_then_success_writes_the_file(pm, monkeypatch):
    base = pm.create("P")
    calls = []

    def flaky(src, dst):
        calls.append(1)
        if len(calls) <= 2:
            raise PermissionError("busy")
        return REAL_REPLACE(src, dst)

    monkeypatch.setattr(profile_manager.os, "replace", flaky)
    pm._write({**base, "name": "after retry"})
    assert len(calls) == 3
    assert pm.load(base["project_id"])["name"] == "after retry"


def test_permission_error_always_raises_keeps_original_and_leaves_no_tmp(
        pm, monkeypatch):
    base = pm.create("P")
    path = pm._path(base["project_id"])
    original = path.read_bytes()
    calls = []

    def locked(src, dst):
        calls.append(1)
        raise PermissionError("locked")

    monkeypatch.setattr(profile_manager.os, "replace", locked)
    with pytest.raises(PermissionError):
        pm._write({**base, "name": "never lands"})
    assert len(calls) == profile_manager.REPLACE_RETRY_ATTEMPTS == 5
    assert path.read_bytes() == original
    assert list(path.parent.glob("*.tmp")) == []


def test_non_permission_errors_are_not_retried(pm, monkeypatch):
    base = pm.create("P")
    calls = []

    def gone(src, dst):
        calls.append(1)
        raise FileNotFoundError("vanished")

    monkeypatch.setattr(profile_manager.os, "replace", gone)
    with pytest.raises(FileNotFoundError):
        pm._write(base)
    assert len(calls) == 1
    assert list(pm._path(base["project_id"]).parent.glob("*.tmp")) == []


def test_archive_write_also_retries(pm, monkeypatch):
    base = pm.create("P")
    calls = []

    def flaky(src, dst):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError("busy")
        return REAL_REPLACE(src, dst)

    monkeypatch.setattr(profile_manager.os, "replace", flaky)
    pm._archive(base)
    assert pm.list_versions(base["project_id"]) == [base["version"]]
