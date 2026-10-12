# Per-Project Pipeline Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the pipeline process each scan batch with the settings and delivery channels of the project the operator picked (and that the batch is filed under), snapshotting them when the batch starts, so changed settings apply to the next batch without restarting the app.

**Architecture:** A new `RuntimeResolver` turns a project id into an immutable `ProjectRuntime` (profile + active channels + trigger pool) read from the `projects` table and cached by `(version, updated_at)`. `Pipeline` takes an optional resolver, resolves the runtime in `submit_batch` BEFORE persisting anything, stores it in the batch's in-memory state, and every stage reads its settings through one helper `_rt(batch_id, project_id)`. With no resolver the pipeline behaves exactly as today (the constructor's profile/channels are the one runtime).

**Tech Stack:** Python 3.11, PyQt6, SQLite/SQLCipher layer, pytest (+ pytest-qt). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-12-pipeline-per-project-design.md` (read it first; this plan implements it).

## Global Constraints

- Backward compatible: with `resolver=None` the pipeline behaves exactly as before; all existing tests (984 at the time of writing) pass unmodified except where a task below says a specific assertion changes.
- Snapshot at batch start: a batch uses its project's settings as they were when it was submitted, for its whole life; the next batch sees updates without a restart.
- No silent fallback: if a project cannot be resolved (row missing, config unreadable or schema-invalid, channel construction fails) the work is refused or failed with a clear reason — never processed or delivered with another project's settings.
- `submit_batch` persists NOTHING (no batch row, no job rows, no project row) when it raises `ProjectUnavailable`.
- Pipeline threads never block the UI thread; the resolver is thread-safe.
- Scope: pipeline + wizard + central-sync messaging only. The Index tab's zones/OCR mode and the Admin CD/DVD burn tab keep using the first/startup project (documented follow-ups).
- Python 3.11, PyQt6; follow the existing code style (type hints, `from __future__ import annotations`, module docstring citing the spec/HLD section).
- End every commit message with the `Co-Authored-By` trailer given in the session's attribution rule.

## Review Focus

- Two batches of DIFFERENT projects in flight at the same time (their pages interleave in the shared stage queues) must each use their own project's separation, QC, export format, DPI and delivery destination. (Tasks 2, 5)
- Editing project A between two batches affects only the second batch; editing it while a batch is mid-flight does not change that batch; the resolver cache returns the SAME runtime (and channel objects, e.g. a CD/DVD channel's burn-queue handle) while the project is unchanged and a NEW one after a save, even for two saves within the same millisecond. (Tasks 1, 2)
- A project whose stored config is broken or missing: `submit_batch` writes nothing and raises; crash recovery fails that batch's jobs cleanly and keeps recovering the others; `resubmit_document` returns False; the other projects keep working. (Tasks 1, 2, 3)
- A review-approved document belonging to project B is exported/delivered with B's settings even when the pipeline's default project is A, both in the same session and after a restart. (Task 3)
- The Scan wizard's project choice reaches the pipeline end to end (real window, real batch), and a `ProjectUnavailable` shows a clear message instead of a traceback. (Tasks 4, 6)

---

## File Structure

| File | Responsibility |
|---|---|
| `core/project_runtime.py` (create) | `ProjectRuntime`, `ProjectUnavailable`, `RuntimeResolver` |
| `data/db.py` (modify) | `Database.get_batch(batch_id)` |
| `core/job_queue.py` (modify) | resolver param, `_rt`, per-batch snapshot, every stage via `_rt`, recover/resubmit |
| `main.py` (modify) | pass `RuntimeResolver(db)` to the pipeline |
| `ui/main_window.py` (modify) | `start_batch` handles `ProjectUnavailable` |
| `core/central_sync.py`, `ui/central_tab.py`, `ui/main_window.py` (modify) | replace the "restart to apply" notice |
| `tests/test_project_runtime.py`, `tests/test_pipeline_multiproject.py` (create) | new tests |
| `.claude/skills/run-dokscanner/…`, docs (modify) | e2e check + wording |

---

### Task 1: `ProjectRuntime` and `RuntimeResolver`

**Files:**
- Create: `core/project_runtime.py`
- Test: `tests/test_project_runtime.py`

**Interfaces:**
- Consumes: `data.db.Database.get_project(project_id) -> Row | None` (columns `project_id, name, version, status, config_json, ..., updated_at`), `core.profile_manager.ProfileManager.validate(config)` (static; raises `ProjectConfigError`), `delivery.registry.active_channels(delivery_cfg) -> list`.
- Produces: `@dataclass(frozen=True) ProjectRuntime(project_id: str, profile: dict, channels: list, trigger_pool: dict)`; `class ProjectUnavailable(Exception)`; `RuntimeResolver(db)` with `.resolve(project_id: str) -> ProjectRuntime` (raises `ProjectUnavailable`), thread-safe, cached per project by `(version, updated_at)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_project_runtime.py`:
```python
import copy
import json
import threading

import pytest

from core.profile_manager import DEFAULT_PROJECT
from core.project_runtime import ProjectRuntime, ProjectUnavailable, RuntimeResolver
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
    cfg = put(db, tmp_path, fmt="tiff", dpi=600)
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

    monkeypatch.setattr("core.project_runtime.active_channels", boom)
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
```
If `ProfileManager.validate` rejects the helper's `scanner.sdk="file"` (look at `core/project_schema.json`), use a value the schema allows and say so in your report; do not weaken the validation.

- [ ] **Step 2: Run tests to verify they fail**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/test_project_runtime.py -o addopts=""`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.project_runtime'`.

- [ ] **Step 3: Implement `core/project_runtime.py`**

```python
"""Per-project runtime for the pipeline (design spec 2026-10-12).

A ProjectRuntime is the immutable bundle a batch needs: the project's stored
config plus its delivery channels. RuntimeResolver builds it from the
`projects` table and caches it by (version, updated_at), so an unchanged
project keeps the SAME runtime (and channel objects) across batches while a
Designer save or central sync yields a fresh one for the next batch.
"""
from __future__ import annotations

import copy
import json
import logging
import threading
from dataclasses import dataclass

from core.profile_manager import ProfileManager, ProjectConfigError
from delivery.registry import active_channels

log = logging.getLogger(__name__)


class ProjectUnavailable(Exception):
    """The project cannot be used for processing (never silently substituted)."""


@dataclass(frozen=True)
class ProjectRuntime:
    project_id: str
    profile: dict
    channels: list
    trigger_pool: dict


class RuntimeResolver:
    def __init__(self, db):
        self.db = db
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[tuple, ProjectRuntime]] = {}

    def resolve(self, project_id: str) -> ProjectRuntime:
        row = self.db.get_project(project_id)
        if row is None:
            raise ProjectUnavailable(f"project {project_id!r} not found")
        stamp = (row["version"], row["updated_at"])
        with self._lock:
            cached = self._cache.get(project_id)
            if cached is not None and cached[0] == stamp:
                return cached[1]
            runtime = self._build(project_id, row)       # inside the lock: one object
            self._cache[project_id] = (stamp, runtime)
            return runtime

    @staticmethod
    def _build(project_id: str, row) -> ProjectRuntime:
        try:
            profile = json.loads(row["config_json"])
        except (TypeError, ValueError) as e:
            raise ProjectUnavailable(
                f"project {project_id!r}: stored config is unreadable") from e
        try:
            ProfileManager.validate(profile)
        except ProjectConfigError as e:
            raise ProjectUnavailable(
                f"project {project_id!r}: stored config is invalid ({e})") from e
        try:
            channels = active_channels(profile.get("delivery", {}))
        except Exception as e:
            raise ProjectUnavailable(
                f"project {project_id!r}: delivery channels unavailable "
                f"({type(e).__name__})") from e
        return ProjectRuntime(project_id=project_id,
                              profile=copy.deepcopy(profile), channels=channels,
                              trigger_pool={c.name: c for c in channels})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/test_project_runtime.py -o addopts=""`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add core/project_runtime.py tests/test_project_runtime.py
git commit -m "feat(pipeline): ProjectRuntime and cached RuntimeResolver"
```

---

### Task 2: Per-batch runtime in `Pipeline` (submit, stages, snapshot semantics)

**Files:**
- Modify: `data/db.py` (add `get_batch`), `core/job_queue.py`
- Test: `tests/test_pipeline_multiproject.py` (create), existing `tests/test_pipeline.py` etc. must pass unmodified

**Interfaces:**
- Consumes: `ProjectRuntime`, `ProjectUnavailable`, `RuntimeResolver` (Task 1).
- Produces: `Database.get_batch(batch_id) -> Row | None`; `Pipeline.__init__(..., resolver=None)` (new LAST keyword); `Pipeline._default` (`ProjectRuntime` built from the constructor's profile/channels/trigger_pool); `Pipeline._resolve(project_id) -> ProjectRuntime` (default runtime when no resolver, else `resolver.resolve`); `Pipeline._rt(batch_id, project_id=None) -> ProjectRuntime`; `submit_batch` raises `ValueError` for a batch whose jobs span several projects (resolver mode only) and `ProjectUnavailable` before persisting anything; documents carry `"project_id"`.

- [ ] **Step 1: Write the failing tests**

`tests/test_pipeline_multiproject.py` (import helpers from `tests.test_project_runtime` and `tests.test_pipeline`):
```python
import copy
import time
from pathlib import Path

import cv2
import pytest

from core.job_queue import Pipeline
from core.models import ScanJob
from core.project_runtime import ProjectUnavailable, RuntimeResolver
from data.db import Database
from delivery.local_folder import LocalFolderChannel
from export.batch_exporter import export_document
from tests.conftest import make_blank_page, make_text_page
from tests.test_pipeline import wait_for
from tests.test_project_runtime import valid_profile


def make_jobs(tmp_path, batch_id, project_id, kinds):
    jobs = []
    for i, kind in enumerate(kinds, start=1):
        img = make_blank_page() if kind == "blank" else make_text_page()
        p = tmp_path / f"{batch_id}_{i}.png"
        cv2.imwrite(str(p), img)
        jobs.append(ScanJob(batch_id=batch_id, project_id=project_id,
                            page_no=i, image_path=str(p)))
    return jobs


@pytest.fixture()
def env(tmp_path):
    db = Database(tmp_path / "mp.db")
    cfg_a = valid_profile("proj-a", "Alpha", tmp_path / "A_out", tmp_path / "A_deliv",
                          fmt="image_pdf", strategy=("blank_page",))
    cfg_b = valid_profile("proj-b", "Bravo", tmp_path / "B_out", tmp_path / "B_deliv",
                          fmt="tiff", strategy=("fixed_pages",))
    cfg_b["separation"]["fixed_pages"] = 1          # every page its own document
    db.insert_project("proj-a", "Alpha", cfg_a)
    db.insert_project("proj-b", "Bravo", cfg_b)
    pipeline = Pipeline(db, cfg_a, [LocalFolderChannel(cfg_a["delivery"]["local_folder"])],
                        export_document, working_dir=tmp_path / "staging",
                        resolver=RuntimeResolver(db))
    pipeline.start()
    yield type("E", (), dict(db=db, pipeline=pipeline, tmp=tmp_path, a=cfg_a, b=cfg_b))
    pipeline.shutdown()
    db.close()


def delivered(env, which):
    return sorted(p.name for p in (env.tmp / f"{which}_deliv").glob("*"))


def docs_done(env, batch_id, n):
    d = env.db.list_documents(batch_id)
    return len(d) == n and all(x["delivery_status"] == "delivered_local" for x in d)


def test_a_batch_is_filed_and_processed_under_the_project_of_its_jobs(env):
    env.pipeline.submit_batch("batch-b", make_jobs(env.tmp, "batch-b", "proj-b", ["text", "text"]))
    assert wait_for(lambda: docs_done(env, "batch-b", 2))
    assert env.db.get_batch("batch-b")["project_id"] == "proj-b"
    assert {d["project_id"] for d in env.db.list_documents("batch-b")} == {"proj-b"}
    assert delivered(env, "B") == ["batch-b_0001.tif", "batch-b_0002.tif"]   # B's format + folder
    assert delivered(env, "A") == []


def test_two_projects_in_flight_at_once_each_use_their_own_settings(env):
    # A: blank-page separation -> [p1 p2] X [p4] = 2 docs, image_pdf into A_deliv
    # B: fixed 1 page/document -> 3 docs, tiff into B_deliv ; submitted back to back
    env.pipeline.submit_batch("batch-a", make_jobs(env.tmp, "batch-a", "proj-a", ["text", "text", "blank", "text"]))
    env.pipeline.submit_batch("batch-b", make_jobs(env.tmp, "batch-b", "proj-b", ["text", "text", "text"]))
    assert wait_for(lambda: docs_done(env, "batch-a", 2) and docs_done(env, "batch-b", 3), 60)
    assert delivered(env, "A") == ["batch-a_0001.pdf", "batch-a_0002.pdf"]
    assert delivered(env, "B") == ["batch-b_0001.tif", "batch-b_0002.tif", "batch-b_0003.tif"]
    assert {d["project_id"] for d in env.db.list_documents("batch-a")} == {"proj-a"}


def test_edit_between_batches_affects_only_the_second(env):
    env.pipeline.submit_batch("one", make_jobs(env.tmp, "one", "proj-a", ["text"]))
    assert wait_for(lambda: docs_done(env, "one", 1))
    cfg = copy.deepcopy(env.a)
    cfg["export"]["format"] = "tiff"
    env.db.update_project("proj-a", "Alpha", cfg, version=2)
    env.pipeline.submit_batch("two", make_jobs(env.tmp, "two", "proj-a", ["text"]))
    assert wait_for(lambda: docs_done(env, "two", 1))
    assert delivered(env, "A") == ["one_0001.pdf", "two_0001.tif"]    # no restart needed


def test_edit_mid_flight_does_not_change_a_running_batch(env):
    gate_in, release = __import__("threading").Event(), __import__("threading").Event()
    real = env.pipeline.export_fn

    def gated(images, export_cfg, ctx, texts=None):
        gate_in.set()
        assert release.wait(30)
        return real(images, export_cfg, ctx, texts)

    env.pipeline.export_fn = gated
    env.pipeline.submit_batch("fly", make_jobs(env.tmp, "fly", "proj-a", ["text"]))
    assert gate_in.wait(30)                       # batch is inside the export stage
    cfg = copy.deepcopy(env.a)
    cfg["export"]["format"] = "tiff"
    env.db.update_project("proj-a", "Alpha", cfg, version=2)
    release.set()
    assert wait_for(lambda: docs_done(env, "fly", 1))
    assert delivered(env, "A") == ["fly_0001.pdf"]       # still the snapshot (image_pdf)


def test_unavailable_project_persists_nothing_and_raises(env):
    jobs = make_jobs(env.tmp, "ghost", "proj-missing", ["text"])
    with pytest.raises(ProjectUnavailable):
        env.pipeline.submit_batch("ghost", jobs)
    assert env.db.get_batch("ghost") is None
    assert env.db.conn.execute("SELECT COUNT(*) FROM jobs WHERE batch_id='ghost'").fetchone()[0] == 0
    assert env.db.get_project("proj-missing") is None


def test_broken_stored_config_blocks_only_that_project(env):
    env.db.conn.execute("UPDATE projects SET config_json='{oops' WHERE project_id='proj-b'")
    env.db.conn.commit()
    with pytest.raises(ProjectUnavailable):
        env.pipeline.submit_batch("bad", make_jobs(env.tmp, "bad", "proj-b", ["text"]))
    env.pipeline.submit_batch("fine", make_jobs(env.tmp, "fine", "proj-a", ["text"]))
    assert wait_for(lambda: docs_done(env, "fine", 1))


def test_mixed_project_ids_in_one_batch_are_refused(env):
    jobs = make_jobs(env.tmp, "mix", "proj-a", ["text"]) + make_jobs(env.tmp, "mix", "proj-b", ["text"])
    with pytest.raises(ValueError, match="one project"):
        env.pipeline.submit_batch("mix", jobs)
    assert env.db.get_batch("mix") is None


def test_documents_are_inserted_with_the_batchs_project_not_the_default(env):
    env.pipeline.submit_batch("meta", make_jobs(env.tmp, "meta", "proj-b", ["text"]))
    assert wait_for(lambda: docs_done(env, "meta", 1))
    assert env.db.list_documents("meta")[0]["project_id"] == "proj-b"      # pipeline default is proj-a
```

Add a legacy-mode guard test in the same file:
```python
def test_without_a_resolver_everything_is_as_before(tmp_path):
    db = Database(tmp_path / "legacy.db")
    cfg = valid_profile("proj-a", "Alpha", tmp_path / "out", tmp_path / "deliv")
    p = Pipeline(db, cfg, [LocalFolderChannel(cfg["delivery"]["local_folder"])],
                 export_document, working_dir=tmp_path / "st")
    p.start()
    try:
        p.submit_batch("legacy", make_jobs(tmp_path, "legacy", "proj-a", ["text"]))
        assert wait_for(lambda: len(db.list_documents("legacy")) == 1 and
                        db.list_documents("legacy")[0]["delivery_status"] == "delivered_local")
        assert db.get_project("proj-a") is not None        # default project row auto-created
    finally:
        p.shutdown()
        db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/test_pipeline_multiproject.py -o addopts=""`
Expected: FAIL — `TypeError: Pipeline.__init__() got an unexpected keyword argument 'resolver'` (and `get_batch` missing).

- [ ] **Step 3: Implement**

`data/db.py` — next to `insert_batch`:
```python
    def get_batch(self, batch_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
```

`core/job_queue.py`:
1. Imports: `from core.project_runtime import ProjectRuntime, ProjectUnavailable`.
2. Constructor: add `resolver=None` as the last parameter; after existing assignments:
```python
        self.resolver = resolver
        # the constructor's profile/channels/trigger_pool ARE the runtime when no
        # resolver is given (legacy behaviour); kept as attributes for compatibility
        self._default = ProjectRuntime(
            project_id=(profile or {}).get("project_id", ""),
            profile=profile, channels=channels, trigger_pool=self.trigger_pool)
```
3. Helpers (after `_emit`):
```python
    def _resolve(self, project_id: str) -> ProjectRuntime:
        """The runtime for a project id. Without a resolver there is exactly
        one runtime (the constructor's). Raises ProjectUnavailable."""
        if self.resolver is None:
            return self._default
        return self.resolver.resolve(project_id)

    def _rt(self, batch_id: str, project_id: str | None = None) -> ProjectRuntime:
        """Settings for a batch: its start-of-batch snapshot if this session
        still holds it, otherwise resolved from the given project id (or the
        batch row's)."""
        with self._lock:
            state = self._batches.get(batch_id)
            runtime = state.get("runtime") if state else None
        if runtime is not None:
            return runtime
        if self.resolver is None:
            return self._default
        if project_id is None:
            row = self.db.get_batch(batch_id)
            project_id = row["project_id"] if row is not None else ""
        return self.resolver.resolve(project_id)
```
4. `submit_batch`:
```python
        if self.resolver is not None:
            project_ids = {j.project_id for j in jobs}
            if len(project_ids) != 1:
                raise ValueError("a batch must belong to exactly one project")
            runtime = self._resolve(next(iter(project_ids)))   # may raise ProjectUnavailable
        else:
            runtime = self._default
            if self.db.get_project(self.profile["project_id"]) is None:
                self.db.insert_project(self.profile["project_id"],
                                       self.profile["name"], self.profile)
        with self._lock:
            self._batches[batch_id] = {"expected": len(jobs), "pages": [],
                                       "counter": 0, "operator": operator,
                                       "runtime": runtime}
        self.db.insert_batch(batch_id, runtime.project_id, operator)
```
(keep the rest of the method as is; the old unconditional project insert/insert_batch lines are replaced).
5. Stage edits — replace every `self.profile` / `self.channels` / `self.trigger_pool` read in stages by the batch's runtime:
   - `_ingest_worker.handle`: `rt = self._rt(job.batch_id, job.project_id)`; `sep_cfg = rt.profile.get("separation", {})`.
   - `_ocr_worker.handle`: `rt = self._rt(record.job.batch_id, record.job.project_id)` and call `self._extract_zone_fields(record, image, rt)`; change that method's signature to `(self, record, image, rt)` and use `rt.profile` for `ocr`/`features`.
   - `_enhance_worker.handle`: `profile = self._rt(record.job.batch_id, record.job.project_id).profile.get("enhancement_profile", {})`.
   - `_qc_worker.handle`: `rt = self._rt(...)`; `qc_cfg = rt.profile.get("qc", {"enabled": False})`; dpi from `rt.profile.get("scanner", {}).get("dpi", 300)`.
   - `_collect_page`: `rt = self._rt(batch_id, record.job.project_id)`; `split_documents(page_dicts, rt.profile.get("separation", {}))`.
   - `_rule_worker.handle`: `rt = self._rt(item["batch_id"])`; `rt.profile.get("rules")`; `self._check_duplicate(document, records, rt)` (signature `(self, document, records, rt)` using `rt.profile`); `insert_document(..., rt.project_id, ...)`; add `"project_id": rt.project_id` to the `document` dict.
   - `_delivery_worker.handle`: `rt = self._rt(document["batch_id"], document.get("project_id"))`; `export_cfg = rt.profile.get("export", {})`; dpi from `rt.profile`; `targets = list(rt.channels)`; `extra = rt.trigger_pool.get(trigger)`.
   - `resubmit_document`: add `"project_id": row["project_id"]` to the `document` dict it builds (the full recovery/resubmit semantics are Task 3).
6. `grep -n "self\.profile\|self\.channels\|self\.trigger_pool" core/job_queue.py` must show only the constructor, `_default` and the legacy branch of `submit_batch` afterwards.

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/test_pipeline_multiproject.py tests/test_pipeline.py tests/test_auto_index.py tests/test_integration.py tests/test_qc_review_flow.py tests/test_delivery.py tests/test_phase3_delivery.py tests/test_benchmark.py -o addopts=""`
Expected: all pass, with no edits to the existing test files. Then run the complete suite once.

- [ ] **Step 5: Commit**

```bash
git add data/db.py core/job_queue.py tests/test_pipeline_multiproject.py
git commit -m "feat(pipeline): per-batch project runtime snapshot; stages use the batch's own settings"
```

---

### Task 3: Recovery and review-approved re-export per project

**Files:**
- Modify: `core/job_queue.py` (`recover`, `resubmit_document`, delivery for batches with no in-memory state)
- Test: `tests/test_pipeline_multiproject.py` (append)

**Interfaces:**
- Consumes: Task 2's `_resolve`, `_rt`, `ProjectUnavailable`.
- Produces: `recover()` resolves each recovered batch's runtime from its jobs' project id (failed jobs + "failed" event + warning when unavailable); `resubmit_document(document_id) -> bool` returns False (and logs) when the document's project is unavailable.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_pipeline_multiproject.py`; reuse `env`, `make_jobs`, `docs_done`, `delivered`)
```python
def test_recovery_uses_each_batchs_own_project(env):
    # simulate a crash: persisted-but-unprocessed jobs of two projects
    for batch, pid in (("rec-a", "proj-a"), ("rec-b", "proj-b")):
        env.db.insert_batch(batch, pid, "op")
        for j in make_jobs(env.tmp, batch, pid, ["text"]):
            env.db.upsert_job(j.job_id, j.batch_id, j.project_id, j.page_no, "in_progress", j.image_path)
    recovered = env.pipeline.recover()
    assert len(recovered) == 2
    assert wait_for(lambda: docs_done(env, "rec-a", 1) and docs_done(env, "rec-b", 1), 60)
    assert delivered(env, "A") == ["rec-a_0001.pdf"] and delivered(env, "B") == ["rec-b_0001.tif"]


def test_recovery_fails_a_batch_whose_project_is_unavailable_and_continues(env, caplog):
    env.db.insert_batch("rec-x", "proj-gone", "op")
    for j in make_jobs(env.tmp, "rec-x", "proj-gone", ["text"]):
        env.db.upsert_job(j.job_id, j.batch_id, j.project_id, j.page_no, "in_progress", j.image_path)
    env.db.insert_batch("rec-a", "proj-a", "op")
    for j in make_jobs(env.tmp, "rec-a", "proj-a", ["text"]):
        env.db.upsert_job(j.job_id, j.batch_id, j.project_id, j.page_no, "in_progress", j.image_path)
    events = []
    env.pipeline.subscribe(events.append)
    recovered = env.pipeline.recover()                   # must not raise
    assert [j.batch_id for j in recovered] == ["rec-a"]
    assert wait_for(lambda: docs_done(env, "rec-a", 1))
    statuses = {r["status"] for r in env.db.conn.execute("SELECT status FROM jobs WHERE batch_id='rec-x'")}
    assert statuses == {"failed"}
    assert any(e["stage"] == "recover" and e["status"] == "failed" for e in events)
    assert "proj-gone" in caplog.text


def _held_document(env, batch, pid):
    """A pending_qc document whose pages exist on disk, ready for review approval."""
    import json
    pages = make_jobs(env.tmp, batch, pid, ["text"])
    env.db.insert_batch(batch, pid, "op")
    env.db.insert_document(f"doc-{batch}", batch, pid, page_range="1-1",
                           enhancement_profile="default", extracted_fields={},
                           validation_status="pending_qc", qc_score=50,
                           qc_detail={"pages": {"1": {"working_path": pages[0].image_path,
                                                      "original_path": pages[0].image_path,
                                                      "ocr_text": ""}}},
                           duplicate_of=None)
    return f"doc-{batch}"


def test_review_approved_document_uses_its_own_projects_settings(env):
    doc_id = _held_document(env, "held-b", "proj-b")          # pipeline default is proj-a
    assert env.pipeline.resubmit_document(doc_id) is True
    assert wait_for(lambda: delivered(env, "B") == ["held-b_0001.tif"])
    assert delivered(env, "A") == []


def test_resubmit_returns_false_when_the_documents_project_is_unavailable(env):
    doc_id = _held_document(env, "held-x", "proj-gone")
    assert env.pipeline.resubmit_document(doc_id) is False
    assert delivered(env, "A") == [] and delivered(env, "B") == []
```
Sabotage (report evidence): make `_rt`/`_resolve` ignore the project id (always the default) and show the recovery and resubmit tests fail.

- [ ] **Step 2: Run tests to verify they fail**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/test_pipeline_multiproject.py -o addopts="" -k "recovery or review or resubmit"`
Expected: FAIL (recovery files everything under the default project / raises on the unavailable one; resubmit uses the default).

- [ ] **Step 3: Implement**

`recover()`:
```python
        recovered = []
        for batch_id, jobs in by_batch.items():
            project_ids = {j.project_id for j in jobs}
            try:
                if len(project_ids) != 1:
                    raise ProjectUnavailable("recovered batch spans several projects")
                runtime = self._resolve(next(iter(project_ids)))
            except ProjectUnavailable as e:
                log.warning("cannot recover batch %s: %s", batch_id, e)
                for job in jobs:
                    self.db.mark_job(job.job_id, "failed")
                self._emit("recover", "failed", batch_id=batch_id, error=str(e))
                continue
            with self._lock:
                self._batches[batch_id] = {"expected": len(jobs), "pages": [],
                                           "counter": 0, "operator": "recovery",
                                           "runtime": runtime}
            for job in jobs:
                self.db.mark_job(job.job_id, "queued")
                self.ingest_q.put(job)
            recovered.extend(jobs)
```
`resubmit_document`: after loading `row`, before building records:
```python
        try:
            self._resolve(row["project_id"])
        except ProjectUnavailable as e:
            log.warning("cannot re-export %s: %s", document_id, e)
            return False
```
(`_delivery_worker` already resolves via `_rt(batch_id, document.get("project_id"))`; for a batch that has no in-memory state it resolves the document's project — no further change needed. Confirm by reading the code and mention it in the report.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/test_pipeline_multiproject.py tests/test_pipeline.py tests/test_qc_review_flow.py -o addopts=""`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add core/job_queue.py tests/test_pipeline_multiproject.py
git commit -m "feat(pipeline): per-project crash recovery and review-approved re-export"
```

---

### Task 4: Wire the resolver into the app; clear message for an unavailable project

**Files:**
- Modify: `main.py`, `ui/main_window.py` (`start_batch`)
- Test: `tests/test_main_window_batch.py` (create)

**Interfaces:**
- Consumes: `RuntimeResolver`, `ProjectUnavailable`, `Pipeline(..., resolver=...)`.
- Produces: `main()` passes `resolver=RuntimeResolver(db)`; `MainWindow.start_batch(batch_id, jobs)` catches `ProjectUnavailable`/`ValueError` from `submit_batch`, shows `QMessageBox.critical(self, "Project not available", ...)` with the reason, logs it, does NOT write the "scan" audit entry and does NOT load pages into the viewer.

- [ ] **Step 1: Write the failing tests** — `tests/test_main_window_batch.py` (read `tests/test_ui_smoke.py` for how `MainWindow` is built with `db, pm, bus, pipeline=..., audit=..., staging_dir=...` and how the admin session is created; reuse that fixture style):
```python
from core.project_runtime import ProjectUnavailable
# fixture builds an admin MainWindow with a fake pipeline object:
class FakePipeline:
    def __init__(self, exc=None):
        self.exc, self.submitted = exc, []
    def submit_batch(self, batch_id, jobs, operator="system"):
        if self.exc:
            raise self.exc
        self.submitted.append((batch_id, [j.project_id for j in jobs], operator))
    # plus whatever MainWindow calls on a pipeline at construction (read ui/main_window.py)

def test_start_batch_submits_the_jobs_of_the_chosen_project(qtbot, window_factory):
    win, pipe, audit = window_factory(FakePipeline())
    win.start_batch("b1", [job("proj-b")])
    assert pipe.submitted == [("b1", ["proj-b"], "admin")]
    assert [r["action"] for r in audit_rows(win)] .count("scan") == 1

def test_unavailable_project_shows_a_message_and_records_nothing(qtbot, window_factory, monkeypatch):
    shown = []
    monkeypatch.setattr("ui.main_window.QMessageBox.critical",
                        staticmethod(lambda *a, **k: shown.append(a[2])))
    win, pipe, audit = window_factory(FakePipeline(ProjectUnavailable("project 'x' not found")))
    win.start_batch("b2", [job("x")])
    assert shown and "not found" in shown[0]
    assert "scan" not in [r["action"] for r in audit_rows(win)]
    assert not win.viewer_has_pages()          # use whatever the viewer exposes (see ui/image_viewer.py)
```
(Define `window_factory`, `job`, `audit_rows`, `viewer_has_pages` from the conventions in the existing UI tests; every helper must be real code in the test file. Sabotage: remove the `except` and show the second test fail.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/test_main_window_batch.py -o addopts=""`
Expected: FAIL (the exception propagates out of `start_batch`).

- [ ] **Step 3: Implement**

`ui/main_window.py` `start_batch` (keep the existing body, wrap the submit):
```python
    def start_batch(self, batch_id: str, jobs: list) -> None:
        user = Session.current_user()
        if self.pipeline is not None:
            try:
                self.pipeline.submit_batch(
                    batch_id, jobs,
                    operator=user.username if user else "unknown")
            except (ProjectUnavailable, ValueError) as e:
                log.warning("batch %s refused: %s", batch_id, e)
                QMessageBox.critical(
                    self, "Project not available",
                    f"This batch was not started: {e}\n\nNothing was recorded. "
                    "Check the project in the Workflow Designer, then scan again.")
                return
        if self.audit:
            ...                      # unchanged
        self.viewer.load_pages([j.image_path for j in jobs])
```
(add `import logging` / `log = logging.getLogger(__name__)` and `from core.project_runtime import ProjectUnavailable` at module level if missing.) `main.py`: `from core.project_runtime import RuntimeResolver`; add `resolver=RuntimeResolver(db),` to the `Pipeline(...)` call. `choose_pipeline_profile` and the default profile stay as the default runtime.

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/test_main_window_batch.py tests/test_ui_smoke.py tests/test_central_ui.py -o addopts=""`
Expected: all pass. Read `main.py` line by line as the interpreter (it is not exercised by tests) and state in the report that names/order are consistent.

- [ ] **Step 5: Commit**

```bash
git add main.py ui/main_window.py tests/test_main_window_batch.py
git commit -m "feat(app): pipeline resolves the project per batch; refused batches show a clear message"
```

---

### Task 5: Central-sync messaging — updates now apply to the next batch

**Files:**
- Modify: `core/central_sync.py` (remove the process-level restart-pending flag and `restart_needed` use), `ui/central_tab.py`, `ui/main_window.py` (status bar), tests that assert "restart"
- Test: update `tests/test_central_ui.py`, `tests/test_central_sync.py`, `tests/test_central_worker.py` where they referenced the restart notice; add the new-wording tests

**Interfaces:**
- Consumes: `SyncResult.applied`, `EventBus.central_synced`.
- Produces: no more "restart pending" state: the Central tab line for a result with `applied` projects reads "Updated project settings were received; they apply to the next scan batch."; the status bar keeps ONLY the "Central: new enrollment code needed" notice; `restart_needed`/`restart_pending()`/`note_result()`/`RESTART_NOTICE` are removed (grep every usage — `core/central_sync.py`, `ui/central_tab.py`, `ui/main_window.py`, `tests/`, `docs/`, `.claude/skills/`).

- [ ] **Step 1: Write/adjust the failing tests**
  - Replace tests asserting the restart notice with: (a) a result with `applied` shows the new "apply to the next scan batch" wording in the Central tab and does NOT mention restart; (b) the message is not sticky (it follows the latest result, which is correct now because nothing is pending); (c) the status bar for admin AND operator roles shows no restart label and still shows the enrollment label after an auth failure; (d) `SyncResult` has no `restart_needed` attribute and the module has no `restart_pending` (guard against leftovers: `assert not hasattr(...)`).
  - Delete tests that only exist to pin the removed sticky-restart behaviour; list each deleted test and why in the report.

- [ ] **Step 2: Run to verify failure** — `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests/test_central_ui.py tests/test_central_sync.py tests/test_central_worker.py -o addopts=""` — Expected: the new wording tests FAIL.

- [ ] **Step 3: Implement** the removals/rewording described under Interfaces. Keep `SyncResult.applied`, the worker/service logic and the generation guard untouched.

- [ ] **Step 4: Run** the three test files plus `tests/test_central_integration.py` — Expected: all pass. Run the complete suite once.

- [ ] **Step 5: Commit**
```bash
git add -A core ui tests
git commit -m "feat(central-sync): project updates apply to the next scan batch (no restart notice)"
```

---

### Task 6: End-to-end through the real app, driver check, docs

**Files:**
- Modify: `.claude/skills/run-dokscanner/driver/check_central.py`, `.claude/skills/run-dokscanner/SKILL.md`, `docs/UAT-checklist.md`, `docs/ENHANCEMENT_PLAN_STATUS.md`, `central/README.md`, `docs/superpowers/specs/2026-10-04-central-api-threat-model.md` ("restart" wording), `docs/superpowers/specs/2026-10-12-pipeline-per-project-design.md` (mark implemented)
- Test: the driver itself + a pytest e2e `tests/test_wizard_project_e2e.py`

**Interfaces:** consumes everything above.

- [ ] **Step 1: Pytest e2e** — `tests/test_wizard_project_e2e.py`: build a real `MainWindow` offscreen with a real `Pipeline(resolver=RuntimeResolver(db))`, two projects with different delivery folders/export formats stored through `ProfileManager.adopt`, drive `ScanWizard`'s output the way the wizard does (call `win.start_batch(batch_id, jobs)` with jobs created through `scanning.file_import` for the SECOND project and one image file), wait for the document, assert it was delivered to the second project's folder in its format and that the batch row has the second project's id. Sabotage: pass `resolver=None` and show it fail (document lands under the default project).
- [ ] **Step 2: Driver check** — extend `check_central.py` (after the managed project is synced): create a one-page PNG, call `win.start_batch(...)` with `scanning.file_import` jobs for the managed project, `h.wait_for` the document row, assert it was delivered into the PC-local delivery folder of THAT project and that the batch row carries its project id; then a central edit (`ocr.lang`/`qc`) followed by a second batch shows the new settings applied without restart (e.g. assert via `db` that the second batch's `qc_detail`/document reflects the new config, or the project version used). Keep the driver's summary honest (recount checks, update SKILL.md). Re-run both documented driver commands from a fresh shell and report the output; LOOK at a screenshot.
- [ ] **Step 3: Docs** — remove every "restart to apply" statement (UAT items 8.x, README, threat model residual risks, status doc "known limitations", the `run-dokscanner` SKILL.md gotcha) and replace by "applies to the next scan batch"; record in the status doc that the wizard-project limitation is FIXED and list the follow-ups (Index tab zones per document project; CD/DVD burn tab per project). Add UAT items: pick the second project in the wizard and confirm delivery per project; edit a project and scan again without restarting.
- [ ] **Step 4: Full verification** — complete suite (`-o addopts=""`), both drivers, `scripts/security_scan.py` (triage anything new in `core/project_runtime.py`).
- [ ] **Step 5: Commit** — `git add -A docs tests .claude && git commit -m "test+docs: wizard project choice end to end; remove restart-to-apply wording"`.

---

## Self-Review

**Spec coverage:** snapshot at batch start (Task 2 `_batches[...]["runtime"]`, tests edit-between/mid-flight); resolver + cache by `(version, updated_at)` incl. same-millisecond case and channel-object reuse (Task 1); no silent fallback + nothing persisted on `ProjectUnavailable` (Tasks 1–2); stages via `_rt` (Task 2); recovery per batch and review-approved re-export per document (Task 3); `main.py` and `start_batch` message (Task 4); central-sync restart wording removed (Task 5); wizard end-to-end + docs (Task 6). Out of scope per spec: Index tab, burn tab.

**Placeholder scan:** Tasks 4's UI test helpers (`window_factory`, `job`, `audit_rows`, `viewer_has_pages`) are explicitly "define from the existing UI test conventions" because they depend on `MainWindow`'s constructor requirements — the implementer must write them as real code (stated in the step); the Task 2 `test_documents_carry_their_project_id...` is flagged to be simplified or removed with no vacuous lines.

**Type consistency:** `ProjectRuntime(project_id, profile, channels, trigger_pool)`, `RuntimeResolver.resolve`, `Pipeline._resolve/_rt`, `Database.get_batch`, `Pipeline(..., resolver=)` are used with the same signatures in Tasks 1–4 and 6.
