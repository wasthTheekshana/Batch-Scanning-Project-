import copy
import json
import threading

import pytest

from projects.profile_manager import DEFAULT_PROJECT, ProfileManager, ProjectConfigError
from pipeline.project_runtime import ProjectRuntime, ProjectUnavailable, RuntimeResolver
from data.db import Database


def valid_profile(pid, name, export_dir, deliver_dir, fmt="image_pdf",
                  strategy=("blank_page",), dpi=300):
    cfg = copy.deepcopy(DEFAULT_PROJECT)
    cfg.update(project_id=pid, name=name, created="2026-10-12T00:00:00+00:00")
    cfg["scanner"].update(sdk="file", dpi=dpi, duplex=False)
    cfg["enhancement_profile"].update(deskew=False, crop=False, clahe=False,
                                      despeckle=False, whiten_bg=False)
    cfg["separation"]["strategy"] = list(strategy)
    cfg["qc"]["stages"] = ["margin", "advanced"]
    cfg["export"].update(format=fmt, filename_template="{batch_id}_{counter:04d}",
                         output_path=str(export_dir))
    cfg["delivery"]["local_folder"] = {"enabled": True, "path": str(deliver_dir)}
    cfg["features"].update(auto_index=False, duplicate_check=False)
    cfg["rules"]["rule_set_id"] = f"rs-{pid}"
    return cfg


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "rt.db")
    yield d
    d.close()


@pytest.fixture()
def resolver(db):
    return RuntimeResolver(db)


def put(db, tmp_path, pid="proj-a", **kw):
    cfg = valid_profile(pid, kw.pop("name", "Alpha"), tmp_path / "out",
                        tmp_path / "deliv", **kw)
    db.insert_project(pid, cfg["name"], cfg)
    return cfg


def test_resolve_builds_runtime_from_the_stored_config(db, resolver, tmp_path):
    put(db, tmp_path, fmt="tiff", dpi=600)
    rt = resolver.resolve("proj-a")
    assert isinstance(rt, ProjectRuntime)
    assert rt.project_id == "proj-a"
    assert rt.profile["export"]["format"] == "tiff"
    assert rt.profile["scanner"]["dpi"] == 600
    assert [c.name for c in rt.channels] == ["local_folder"]
    assert set(rt.trigger_pool) == {"local_folder"}
    assert rt.trigger_pool["local_folder"] is rt.channels[0]


def test_unknown_project_is_unavailable(resolver):
    with pytest.raises(ProjectUnavailable, match="proj-x"):
        resolver.resolve("proj-x")


def test_unreadable_json_is_unavailable(db, resolver):
    db.insert_project("bad-json", "Bad", {"placeholder": True})
    db.conn.execute("UPDATE projects SET config_json='{not json' WHERE project_id='bad-json'")
    db.conn.commit()
    with pytest.raises(ProjectUnavailable):
        resolver.resolve("bad-json")


def test_schema_invalid_config_is_unavailable(db, resolver):
    db.insert_project("bad-schema", "Bad", {"project_id": "bad-schema", "name": "Bad"})
    with pytest.raises(ProjectUnavailable):
        resolver.resolve("bad-schema")


def test_one_broken_project_does_not_affect_the_others(db, resolver, tmp_path):
    put(db, tmp_path, "proj-a")
    db.insert_project("bad-schema", "Bad", {"project_id": "bad-schema"})
    with pytest.raises(ProjectUnavailable):
        resolver.resolve("bad-schema")
    assert resolver.resolve("proj-a").project_id == "proj-a"


def test_unchanged_project_returns_the_same_runtime_object(db, resolver, tmp_path):
    put(db, tmp_path)
    first = resolver.resolve("proj-a")
    assert resolver.resolve("proj-a") is first
    assert resolver.resolve("proj-a").channels[0] is first.channels[0]


def test_saved_project_gets_a_new_runtime(db, resolver, tmp_path):
    cfg = put(db, tmp_path)
    first = resolver.resolve("proj-a")
    cfg2 = copy.deepcopy(cfg)
    cfg2["export"]["format"] = "tiff"
    db.update_project("proj-a", "Alpha", cfg2, version=2)
    second = resolver.resolve("proj-a")
    assert second is not first
    assert second.profile["export"]["format"] == "tiff"
    assert first.profile["export"]["format"] == "image_pdf"   # old snapshot untouched


def test_two_saves_with_identical_timestamps_still_invalidate(db, resolver, tmp_path, monkeypatch):
    cfg = put(db, tmp_path)
    first = resolver.resolve("proj-a")
    monkeypatch.setattr("data.db._now", lambda: "2026-10-12T00:00:00.000+00:00")
    cfg2 = copy.deepcopy(cfg)
    cfg2["ocr"]["lang"] = "deu"
    db.update_project("proj-a", "Alpha", cfg2, version=2)
    a = resolver.resolve("proj-a")
    cfg3 = copy.deepcopy(cfg)
    cfg3["ocr"]["lang"] = "fra"
    db.update_project("proj-a", "Alpha", cfg3, version=3)     # same updated_at, new version
    b = resolver.resolve("proj-a")
    assert a is not first and b is not a
    assert b.profile["ocr"]["lang"] == "fra"


def test_profile_is_isolated_from_later_db_changes(db, resolver, tmp_path):
    put(db, tmp_path)
    rt = resolver.resolve("proj-a")
    before = json.dumps(rt.profile, sort_keys=True)
    db.conn.execute("UPDATE projects SET config_json='{}' WHERE project_id='proj-a'")
    db.conn.commit()
    assert json.dumps(rt.profile, sort_keys=True) == before


def test_channel_construction_failure_is_unavailable(db, resolver, tmp_path, monkeypatch):
    put(db, tmp_path)

    def boom(_cfg):
        raise OSError("disk offline")

    monkeypatch.setattr("pipeline.project_runtime.active_channels", boom)
    with pytest.raises(ProjectUnavailable, match="OSError"):
        resolver.resolve("proj-a")


def test_resolver_is_thread_safe_and_returns_one_object(db, resolver, tmp_path):
    put(db, tmp_path)
    results, barrier = [], threading.Barrier(8)

    def work():
        barrier.wait()
        results.append(resolver.resolve("proj-a"))

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(results) == 8 and all(r is results[0] for r in results)


def test_concurrent_cold_resolves_build_exactly_once(db, resolver, tmp_path, monkeypatch):
    """Two threads race on a cold cache; the first is held inside the build
    until the second has had every chance to enter it. Only a lock around
    check+build keeps the build count at one."""
    put(db, tmp_path)
    real_validate = ProfileManager.validate
    builds, in_build = [], threading.Event()

    def slow_validate(cfg):
        builds.append(1)
        in_build.set()
        threading.Event().wait(0.5)       # window for a racing second builder
        return real_validate(cfg)

    monkeypatch.setattr("pipeline.project_runtime.ProfileManager.validate", staticmethod(slow_validate))
    results = []

    def work():
        results.append(resolver.resolve("proj-a"))

    t1 = threading.Thread(target=work)
    t1.start()
    assert in_build.wait(10)
    t2 = threading.Thread(target=work)
    t2.start()
    t1.join(10)
    t2.join(10)
    assert len(results) == 2 and results[0] is results[1]
    assert len(builds) == 1


SECRET = "S3CRET-VALUE-123"


@pytest.mark.parametrize("path, mutate", [
    ("export/format", lambda c: c["export"].update(format=SECRET)),
    ("scanner/dpi", lambda c: c["scanner"].update(dpi=SECRET)),
    ("scanner/sdk", lambda c: c["scanner"].update(sdk=SECRET)),
])
def test_invalid_config_message_has_the_path_but_never_the_value(db, resolver, tmp_path, path, mutate):
    cfg = valid_profile("proj-s", "Secret", tmp_path / "out", tmp_path / "deliv")
    mutate(cfg)
    db.insert_project("proj-s", "Secret", cfg)
    with pytest.raises(ProjectUnavailable) as ei:
        resolver.resolve("proj-s")
    assert path in str(ei.value)
    assert SECRET not in str(ei.value)
    assert isinstance(ei.value.__cause__, ProjectConfigError)   # detail stays on the chain


def test_unreadable_json_message_does_not_echo_the_raw_text(db, resolver):
    db.insert_project("bad-json", "Bad", {"placeholder": True})
    db.conn.execute("UPDATE projects SET config_json=? WHERE project_id='bad-json'",
                    ("{" + SECRET,))
    db.conn.commit()
    with pytest.raises(ProjectUnavailable) as ei:
        resolver.resolve("bad-json")
    assert SECRET not in str(ei.value)


def test_returned_runtime_is_the_cached_object_even_after_a_caller_mutates_it(db, resolver, tmp_path):
    """Callers must treat runtime.profile as read-only: the cache hands out the
    very same object, so a mutation is visible to the next batch."""
    put(db, tmp_path)
    rt = resolver.resolve("proj-a")
    rt.profile["export"]["format"] = "tiff"
    again = resolver.resolve("proj-a")
    assert again is rt
    assert again.profile["export"]["format"] == "tiff"
