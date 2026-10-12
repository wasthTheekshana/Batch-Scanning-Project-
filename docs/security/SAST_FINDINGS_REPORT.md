# SAST Findings Report — DOK Scanner

**Tool:** Bandit 1.9.4 (Python static analysis security scanner)
**Scope:** entire application source (`core/`, `data/`, `delivery/`, `export/`,
`imaging/`, `ocr/`, `qc/`, `scanning/`, `separation/`, `ui/`) — 7,101 lines as
of the most recent scan. Excluded: `.venv/`, `tests/`, `.claude/`, `dist/`,
`build/` (dependencies, test code, and build artifacts are not in scope for
this scan).
**Original scan date:** 2026-08-29
**Re-scan #1 (2026-08-30):** re-run after a full-page OCR extraction-mode
feature (`ocr.mode=full_page`, spanning `ocr/`, `ui/zone_dialog.py`,
`ui/index_editor.py`, `ui/workflow_designer.py`) merged from a separate
branch/session. Identical finding set to the original scan — same 16
findings, same files, same line numbers (confirmed by diffing the
`Location:` lines, not just summary counts). The new feature introduced
zero new SAST findings.
**Re-scan #2 (2026-08-30):** re-run via the new `scripts/security_scan.py`
runner (see "How to run this yourself" below). 2 new Low-severity findings
appeared — both in the new runner script itself (`scripts/security_scan.py`
uses `subprocess` to invoke Bandit). Triaged below alongside the other
subprocess findings; same disposition, no code change needed.
**Raw output:** `docs/security/sast_bandit_report.json` (machine-readable),
`docs/security/sast_bandit_report.txt` (human-readable, full detail) — both
reflect the most recent (re-scan #2) run.

## How to run this yourself

```powershell
.venv\Scripts\python.exe scripts\security_scan.py
```

This overwrites `docs/security/sast_bandit_report.json` and `.txt` with a
fresh scan every time — it does not keep old copies around, so if you want
to compare against a prior run, check it out from git history
(`git log -- docs/security/sast_bandit_report.txt`) before re-running.
After running, re-read this file (`SAST_FINDINGS_REPORT.md`) and check
whether the finding set changed — the script prints a reminder to do this,
but doesn't update this write-up automatically, since a human (or an
assistant working with you) should look at any new finding and decide its
disposition, not have it silently pre-classified.

This satisfies Enhancement Plan §7's "Static Application Security Testing
(SAST)" line item: *"An actual scan run against the codebase ... with a
findings report and remediation status."* Per the plan's own caution, this
report states only what has actually been run and found — not a claim of a
mature security program. It also stands as the first evidence toward
§7.1's *"repeat SAST/DAST/infra assessments on the cadence agreed with the
Bank"* — this re-scan is that repetition happening in practice, triggered by
new code landing, not yet on a fixed calendar cadence.

## Summary

| Severity | Original scan (before remediation) | Current (re-scan #2) |
|---|---|---|
| High | 4 | **0** |
| Medium | 3 | 3 |
| Low | 13 | 15 (+2, both in the new scan-runner script itself) |

One class of finding (weak-hash usage) was fixed during this pass. Every
other finding is triaged below with a disposition — none are silently
dismissed.

## Fixed

### Weak hash (MD5) used without `usedforsecurity=False` — 4 instances

**Files:** `delivery/burn_queue.py:17`, `delivery/local_folder.py:12`,
`delivery/sftp_channel.py:38`, `ocr/duplicate.py:18`
**Bandit rule:** B324 (CWE-327), Severity High / Confidence High

**Disposition: fixed.** All four uses are file-integrity checksums (verifying
a CD/DVD burn matches its source, confirming a delivered file wasn't
corrupted in transit, detecting duplicate scanned pages) — not password
hashing, not authentication, not protecting against a deliberate adversary
forging a match. MD5 is adequate for detecting accidental corruption; it is
inadequate against a deliberate collision attack, which is not the threat
model for any of these four call sites. Added `usedforsecurity=False`
(Python 3.9+) to each call, which documents that intent explicitly and
removes the finding rather than suppressing it blindly. Verified: existing
tests for burn verification, local/SFTP delivery checksums, and duplicate
detection all still pass unchanged (23/23 in the directly affected test
files, full suite unaffected).

## Reviewed and accepted — no code change

### Hardcoded-password false positives — 5 instances

**Files:** `core/profile_manager.py:46`, `ui/workflow_designer.py:35,37,39`,
`ui/theme.py:23`
**Bandit rule:** B105 (CWE-259), Severity Low / Confidence Medium

**Disposition: false positive, no action.** Bandit's heuristic flags any
short string literal near a variable/key name containing "pass" as a
possible hardcoded password. Every instance here is either a QC
`pass_score` integer (a scoring threshold, 65-90) or a hex color constant
named `PASS` (this codebase's "pass" status color in the UI theme, not a
credential). None of these are secrets. No `#nosec` suppression comment was
added — leaving the finding visible in the raw scan output is preferable to
silently suppressing it, so a future reviewer can independently confirm the
same conclusion rather than trusting an unexplained suppression.

### SQL built with an f-string — 3 instances

**Files:** `data/db.py:277`, `data/db.py:291`, `export/qc_report.py:37`
**Bandit rule:** B608 (CWE-89), Severity Medium / Confidence Medium

**Disposition: reviewed, not injectable, no action.** All three interpolate
only column names or a fixed count of `?` placeholders into the query text —
never a value. `data/db.py:277`'s `cols` comes from `_DOC_COLUMNS`, a
hardcoded set of allowed column names checked earlier in the same function
(`insert_document` raises `ValueError` on any other key); `data/db.py:291`'s
`sets` is built the same way in `update_document`. `export/qc_report.py:37`'s
placeholder string is `",".join("?" * len(doc_ids))` — a count-based repeat
of the literal placeholder character, not user data. Every actual value in
all three queries is passed through the parameterized `?` binding
(`tuple(base.values())` / `tuple(vals)` / `tuple(doc_ids)`), which is what
actually prevents SQL injection. Python's DB-API has no parameterization
mechanism for identifiers (column/table names) — this f-string pattern is
the standard, necessary way to build a dynamic column list safely, provided
(as here) the identifiers themselves never come from unvalidated external
input.

### SQL built with string formatting in `custody/timeline.py` — 3 instances

**File:** `custody/timeline.py:42`, `custody/timeline.py:66`, `custody/timeline.py:86`
**Bandit rule:** B608 (CWE-89), Severity Medium / Confidence Medium
(reported in re-scan #3; see the note under "Central service" below)

**Disposition: reviewed, false positive, no action.** Only two kinds of text
are ever interpolated into the SQL: (i) at line 42, the `where` fragment,
which both callers pass as one of two internal literals (`"bundle_id=?"` or
`"batch_id=?"`, `timeline.py:107` and `:116`) and never as caller- or
user-supplied text; and (ii) at lines 66 and 86 (and the clause builders just
above them), strings of `?` placeholders built from list lengths
(`",".join("?" * len(ids))`) plus fixed clause literals. No user data
appears in any SQL text; every actual value (bundle id, batch ids, document
ids, the mirrored-action constant) is passed through the parameterized `?`
binding. Same reasoning as the `data/db.py` / `export/qc_report.py` entry
above. Code unchanged.

### Subprocess usage — 6 instances

**Files:** `delivery/cddvd_channel.py:20,111`, `ui/admin_panel.py:10,36`,
`scripts/security_scan.py:13,23` (new in re-scan #2)
**Bandit rules:** B404, B603, B607 (CWE-78), Severity Low / Confidence High

**Disposition: reviewed, already mitigated, no further action.** These
invoke the configured CD/DVD burner command and Windows `icacls` (for
restricting the exported master-key file to the current user) via
`subprocess.run` with an argument list, not `shell=True` — so shell
metacharacter injection is not possible through the normal
argument-passing path. The disc-label value that feeds into the burner
command was specifically hardened against shell-metacharacter and
strict-pattern injection in prior work (`git log --grep` shows two
dedicated security-fix commits: *"reject shell metacharacters in disc
labels before burner command interpolation"* and *"validate disc label
against strict pattern before burner command interpolation"*). `icacls` is
invoked with a fixed argument list and no user-controlled command
structure. B607 (partial executable path) is worth a one-line note: `icacls`
is resolved via `PATH`, which is standard practice for a well-known Windows
system utility, and not a call to an admin-configurable path.

The two new findings are `scripts/security_scan.py` itself — the runner
that produces this very report calls `subprocess.run([sys.executable, "-m",
"bandit", ...])` to invoke Bandit. Same shape as the others: a fully
hardcoded argument list (no string built from external input), no
`shell=True`. Bandit flagging its own invocation wrapper is expected, not a
defect.

### Bare `except: pass` on cleanup paths — 2 instances

**Files:** `delivery/sftp_channel.py:118`, `scanning/twain_source.py:103`
**Bandit rule:** B110 (CWE-703), Severity Low / Confidence High

**Disposition: reviewed, acceptable, minor improvement possible.** Both
swallow exceptions while closing/destroying a resource (an SFTP transport,
a TWAIN scanner handle) that is already being torn down — typically during
error-path cleanup where the original error is what matters, not a
secondary failure to close a handle that's going away regardless. Not a
security defect. A `log.debug(...)` instead of a bare `pass` would aid
future debugging without changing behavior; left as a minor, non-blocking
follow-up rather than bundled into this security pass.

### `assert` used for a runtime invariant — 1 instance

**File:** `ui/custody_panel.py:20`
**Bandit rule:** B101 (CWE-703), Severity Low / Confidence High

**Disposition: reviewed, intentional, no action.** This asserts that the
UI's custody-checkpoint stage lists stay in sync with the domain layer's
`VALID_STAGES` set — a developer-facing consistency guard, not a security
control, and not something that should ever be false in a passing build (if
it were, the test suite's own custody tests would already have failed).
Bandit's general caution about `assert` (stripped under `python -O`) is
valid Python hygiene advice but doesn't change this: this codebase's build
process (PyInstaller, see `docscanner.spec`) does not compile with `-O`, so
the assertion is not silently removed in the shipped executable.

## Central service (`central/`)

**Re-scan #3 (2026-10-08):** re-run via `scripts/security_scan.py` after the
new network-facing `central/` FastAPI service (Phase 1 of the central
dashboard) landed. Unlike the desktop application, this component is
reachable over the network, so it is also covered by a threat model:
`docs/superpowers/specs/2026-10-04-central-api-threat-model.md` (SDLC A.6).

**Scope:** all 17 files in `central/` (882 lines of code, as counted by
Bandit's per-file metrics). The scan runs over the whole repository with the
same exclusions as before (`tests/` is not scanned).

**Result for `central/`: 0 findings** (High 0, Medium 0, Low 0, no `#nosec`
suppressions). Every `central/` file reports zero issues in the per-file
metrics of `sast_bandit_report.json`.

| Severity | Findings in `central/` |
|---|---|
| High | 0 |
| Medium | 0 |
| Low | 0 |

**Disposition: nothing to fix, nothing to accept.** Notes on why classes that
Bandit commonly flags in web services are absent, so the clean result is not
over-read:

- *SQL injection (B608):* `central/` uses SQLAlchemy ORM / Core constructs
  with bound parameters only; there are no hand-built SQL strings.
- *Hardcoded passwords (B105/B106):* none. The one bcrypt "dummy hash" in
  `security.py` is computed at import time from a fixed string and is used
  only for timing equalisation; it is not a credential. Secrets
  (`CENTRAL_SESSION_SECRET`, DB URL) are read from the environment and
  `Settings.from_env` rejects a session secret under 32 characters.
- *Weak hash (B324):* SHA-256 is used for API keys and enrollment codes,
  which are 96-256 bit random values (an unsalted fast hash is appropriate
  for high-entropy secrets and permits an indexed lookup). Passwords use
  bcrypt over a SHA-256 pre-hash.
- *Subprocess, `eval`, `pickle`, `yaml.load`, `assert`:* none used.

**Limits of this evidence:** Bandit is a pattern scanner. It does not assess
authentication logic, CSRF, session handling, race conditions or business
rules; those are addressed by the threat model and the tests it cites. No
DAST or infrastructure assessment of the running service has been done yet
(the "DAST / infra" line in the sequencing checklist below remains open, and
now has a network-facing component to apply to).

**Outside `central/` (not triaged in this section):** the same re-scan also
reports 3 new Medium B608 (string-built SQL) findings in
`custody/timeline.py` (lines 42, 66, 86), from the custody-timeline feature
merged since re-scan #2. They are out of scope for the central-service
review; their disposition (false positive, no action) is recorded in the
"SQL built with string formatting in `custody/timeline.py`" entry above. The repository-wide totals
in the raw reports are now High 0 / Medium 6 / Low 15 (previously 0 / 3 / 15).
The updated raw reports `sast_bandit_report.json` / `.txt` reflect this run.

## Central sync client (`core/central_*.py` and related)

**Re-scan #4 (2026-10-09):** re-run via `scripts/security_scan.py` after the
PC-side sync client for the central service landed.

**Scope (lines of code per Bandit's per-file metrics):** `core/central_client.py`
(141), `core/central_state.py` (111), `core/central_sync.py` (306),
`core/config_overlay.py` (74), `core/safe_paths.py` (39), `ui/central_tab.py`
(178).

**Result for these files: 0 findings** (High 0, Medium 0, Low 0, no `#nosec`).
Repository-wide totals are unchanged from re-scan #3: High 0 / Medium 6 /
Low 15, so no new finding exists anywhere.

| Severity | Findings in the new files |
|---|---|
| High | 0 |
| Medium | 0 |
| Low | 0 |

**Disposition: nothing to fix, nothing to accept.** Notes so the clean result
is not over-read:

- *Network (B310, urllib scheme audit):* not raised; `CentralClient` accepts
  only `https://` (or loopback `http://` behind an explicit environment
  switch) before any request is built, and TLS verification is never disabled.
- *Hardcoded passwords (B105/B106):* none; the API key is held in the DPAPI
  keystore, never in source or settings.
- *Broad `except` (B110/B112):* the sync paths catch `Exception` deliberately
  but log (type name only, never message text that could hold key data) and
  convert to a `SyncResult`; Bandit does not flag them because they do not
  `pass`.

**Limits of this evidence:** Bandit does not assess the path-traversal guards
(`safe_paths`, `config_overlay`), the redirect/size handling or the sync race
handling; those are pinned by `tests/test_central_*.py`,
`tests/test_safe_paths.py` and the end-to-end `tests/test_central_integration.py`.
The client-side threat model is section 7 of
`docs/superpowers/specs/2026-10-04-central-api-threat-model.md`.

## Recommended sequencing (per Enhancement Plan §7.1)

- [x] Draft the SDLC policy document and security testing scope statement
- [x] Run an initial SAST scan and produce this findings report
- [ ] Make the internal decision on SOC coverage (in-house vs. third-party
  MSSP) — business decision, not addressed here
- [ ] Run the first DAST scan and infrastructure vulnerability assessment
  once a network-facing component exists (currently none — this is a
  fully offline desktop application; DAST/infra assessment apply once
  §3.7 API or §3.3 Multi-Site Consolidation are built, both currently
  deferred)
- [ ] Repeat SAST/DAST/infra assessments on an agreed cadence, with an
  independent tester each cycle — a one-time internal scan (this report)
  does not satisfy the plan's "independence of testing" requirement, which
  needs a separate QA/security function or an external tester

## Per-project pipeline (`core/project_runtime.py`, `core/job_queue.py`)

**Re-scan #5 (2026-10-12):** re-run via `scripts/security_scan.py` after the
pipeline started resolving each batch's project at batch start.

**Result for the new/changed files: 0 findings** (High 0, Medium 0, Low 0, no
`#nosec`) in `core/project_runtime.py` and `core/job_queue.py`; the changed
`ui/main_window.py` and `ui/central_tab.py` are also clean. Repository-wide
totals are unchanged from re-scan #4: High 0 / Medium 6 / Low 15; the raw
reports differ only by line-number shifts in already-triaged findings.

**Disposition: nothing to fix, nothing to accept.** Not covered by a static
scan: `ProjectUnavailable` messages carry only field paths and ids (covered by
`tests/test_project_runtime.py` and `tests/test_main_window_batch.py`), and
recovery fails closed (covered by `tests/test_pipeline_multiproject.py`); see
section 7 of the central-API threat model.
