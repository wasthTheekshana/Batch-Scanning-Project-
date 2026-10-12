# Per-Project Pipeline Configuration — Design Spec

Status: approved by user 2026-10-12 (decisions below), ready for implementation planning.
Follows from `docs/ENHANCEMENT_PLAN_STATUS.md` ("the scan wizard's project choice is not
applied to the pipeline") and the central-sync work, whose "restart to apply" limitation
comes from the same cause.

## Problem

`core/job_queue.py` `Pipeline` is built once at startup with ONE project profile and one
list of delivery channels. Every stage reads `self.profile` (separation, OCR/zones, QC,
enhancement, rules, export, scanner DPI, duplicate check, project id) and delivery uses
`self.channels` / `self.trigger_pool`. Consequences:

1. The Scan wizard lets the operator pick a project, but the batch is processed (and filed in
   the database) under the startup project regardless.
2. A PC with several projects (the normal case once central assigns more than one) cannot
   really use them.
3. Changed project settings (Designer save, central sync) only reach scanning after an
   application restart. The central-sync UI says "restart to apply".
4. Review-approved documents (`resubmit_document`) are exported with the startup project's
   settings even if they belong to another project.

## Decisions (user, 2026-10-12)

- **Snapshot at batch start.** A batch uses its project's settings as they were when it was
  submitted, for its whole life. The next batch sees updates immediately; no restart.
  (Rejected: live per-document lookup — a mid-batch edit could split one batch across two
  configurations.)
- **Scope: pipeline + wizard only.** The Index tab (zones / OCR mode taken from the first
  project) and the Admin CD/DVD burn tab (handles the startup project's disc queue only)
  are unchanged and recorded as follow-ups.

## Design

### ProjectRuntime and the resolver (`core/project_runtime.py`, new)

```python
@dataclass(frozen=True)
class ProjectRuntime:
    project_id: str
    profile: dict              # the project's full config (as stored)
    channels: list             # delivery.registry.active_channels(profile["delivery"])
    trigger_pool: dict         # name -> channel, same semantics as today's default

class ProjectUnavailable(Exception): ...   # unknown id, unreadable/invalid stored config

class RuntimeResolver:
    def __init__(self, db): ...
    def resolve(self, project_id: str) -> ProjectRuntime
```

- Built from the `projects` row (`config_json`), validated once with `ProfileManager.validate`.
- Cached per `project_id`, keyed by the row's `(version, updated_at)`: while a project is
  unchanged the SAME runtime (and therefore the same channel objects, e.g. a CD/DVD channel's
  burn-queue handle) is reused across batches; a Designer save or central sync bumps the row
  and the next `resolve` builds a fresh runtime. Thread-safe (lock); resolution happens on
  the submitting thread and on the pipeline threads for recovery/re-export.
- Any failure (row missing, JSON unreadable, schema-invalid, channel construction error) raises
  `ProjectUnavailable` with a short reason. There is NO silent fallback to another project's
  settings — delivering a document to the wrong destination is worse than refusing.

### Pipeline (`core/job_queue.py`)

- `Pipeline(db, profile, channels, export_fn, ..., resolver=None)`; the existing
  `profile`/`channels`/`trigger_pool` arguments define the DEFAULT runtime (kept so every
  existing test and `scripts/benchmark.py` work unchanged). With `resolver=None` every batch
  uses the default runtime — today's behaviour exactly.
- `submit_batch(batch_id, jobs, operator)`: the batch's project id is the (single) `project_id`
  carried by its jobs (mixed ids → `ValueError`). The runtime is resolved BEFORE anything is
  persisted; `ProjectUnavailable` propagates to the caller and nothing is written. The runtime
  snapshot is stored in `self._batches[batch_id]["runtime"]` and the batch row is inserted under
  that project id (today it is always the startup project).
- Every stage obtains its settings through one helper `self._rt(batch_id, project_id=None)`
  (batch state first; else resolve by the given project id or the batch row's project id; else
  the default runtime when no resolver). Mapping of today's `self.profile`/`self.channels` uses:
  ingest (`separation`), OCR zone extraction (`ocr`, `features.auto_index`), enhance
  (`enhancement_profile`), QC (`qc`, `scanner.dpi`), `_collect_page` (`separation`), rule stage
  (`rules`, project id, duplicate check), delivery (`export`, `scanner.dpi`, channels,
  trigger pool).
- `recover()` resolves each recovered batch's runtime from its jobs' `project_id`; if that
  fails the batch's jobs are marked failed, a "failed" event is emitted and a warning logged.
- `resubmit_document` and the delivery stage for batches with no in-memory state (documents
  approved in a later session) resolve the runtime from the DOCUMENT's `project_id`; failure
  → `resubmit_document` returns False (as for "page images unavailable") and logs.

### Callers

- `main.py`: builds `RuntimeResolver(db)` and passes it; the existing startup profile stays the
  default runtime. `choose_pipeline_profile` keeps its role (default runtime only).
- `ui/main_window.start_batch`: a `ProjectUnavailable` from `submit_batch` shows a critical
  message box ("Project not available: <reason>"); the wizard's temp files are left for the
  operator (no data is lost, nothing was persisted).
- Central-sync messaging: scanning no longer needs a restart. The persistent "restart pending"
  notice/status-bar label is replaced by wording that says updates apply to the NEXT batch;
  docs, UAT checklist and threat-model "restart to apply" statements are updated.

### Non-goals / follow-ups

- Index tab zones / OCR mode per document project; CD/DVD burn tab for every project's queue.
- Hot-folder watchers (none exist as a submit path today); moving documents between projects.

## Testing

- Two projects with different `export.format`, `delivery.local_folder.path`, `separation`
  strategy and `scanner.dpi` run through ONE pipeline instance in one run; each document lands
  in its own project's folder/format and the batch rows carry the right project ids.
- Snapshot semantics: editing project A between two batches affects only the second; editing
  A while a batch is mid-flight does not change that batch.
- Resolver cache: unchanged project → same runtime object (identity); saved project → new one;
  broken stored config → `ProjectUnavailable`, no other project affected.
- `ProjectUnavailable` in `submit_batch` persists nothing; in `recover()` fails the batch's jobs
  without crashing; in `resubmit_document` returns False.
- Backward compatibility: all existing pipeline/integration tests pass unmodified with
  `resolver=None`.
- End-to-end through the real app (run-dokscanner driver): pick the second project in the wizard
  and confirm documents are exported/delivered per that project.
