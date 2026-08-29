# SAST Findings Report — DOK Scanner

**Tool:** Bandit 1.9.4 (Python static analysis security scanner)
**Scope:** entire application source (`core/`, `data/`, `delivery/`, `export/`,
`imaging/`, `ocr/`, `qc/`, `scanning/`, `separation/`, `ui/`) — 6,959 lines.
Excluded: `.venv/`, `tests/`, `.claude/`, `dist/`, `build/` (dependencies, test
code, and build artifacts are not in scope for this scan).
**Date:** 2026-08-29
**Raw output:** `docs/security/sast_bandit_report.json` (machine-readable),
`docs/security/sast_bandit_report.txt` (human-readable, full detail)

This satisfies Enhancement Plan §7's "Static Application Security Testing
(SAST)" line item: *"An actual scan run against the codebase ... with a
findings report and remediation status."* Per the plan's own caution, this
report states only what has actually been run and found — not a claim of a
mature security program.

## Summary

| Severity | Before remediation | After remediation |
|---|---|---|
| High | 4 | **0** |
| Medium | 3 | 3 |
| Low | 13 | 13 |

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

### Subprocess usage — 4 instances

**Files:** `delivery/cddvd_channel.py:20,111`, `ui/admin_panel.py:10,36`
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
