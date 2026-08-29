# Secure Software Development Lifecycle Policy — DOK Scanner

**Status:** Draft, reflecting actual current practice on this project.
**Date:** 2026-08-29

This document describes the development process actually followed for DOK
Scanner, so it can be presented as evidence per Enhancement Plan §7's
"Secure Software Development Process (SDLC)" requirement. It is written from
what has genuinely been practiced, not an idealized policy aspiration —
per the plan's own warning against claiming a process that isn't real.

## 1. Development Process

Every change follows the same sequence, regardless of size:

1. **Requirements are clarified before code is written.** A change that
   touches only existing, well-understood code is scoped and implemented
   directly with test coverage. A change that introduces new architecture
   or a new subsystem goes through a design phase first: the intended
   approach is written down (`docs/superpowers/specs/`), reviewed for
   internal consistency, and only then turned into an implementation plan
   (`docs/superpowers/plans/`) before any code is written.
2. **Test-driven development.** For every function or user-facing behavior
   added, the test is written first, run to confirm it fails for the
   expected reason, then the implementation is written to make it pass. The
   project's test suite (300 tests as of this document) exercises real
   behavior — a real SQLite database, real Qt widget interaction, real PDF
   generation — rather than mocking the code under test.
3. **Isolated development for multi-step work.** Features large enough to
   span several commits are built in an isolated git worktree on their own
   branch, never directly on `main`, so `main` never carries in-progress
   work.
4. **Code review before merge — two layers:**
   - *Per-change review*: each discrete piece of work is reviewed against
     its own stated requirements (spec compliance) and for code quality
     (clean separation of concerns, test coverage, no unjustified
     complexity) before being considered done.
   - *Whole-branch review before merge*: once every individual piece of a
     larger feature is complete, the entire branch is reviewed as one unit,
     specifically looking for integration problems that per-change review
     cannot see — e.g. two pieces built independently that don't correctly
     interoperate, a config value merged incorrectly across a boundary,
     signature drift between a function's definition and its later callers.
     This layer has caught real defects that individual-change review
     missed, including one that would have crashed the application on first
     use in production (a hardcoded placeholder value from test fixtures
     that never reached a real deployment scenario during unit-level
     testing).
   - Findings from either review layer are triaged by severity
     (Critical/Important/Minor); Critical and Important findings must be
     resolved (fixed, or explicitly ruled acceptable with a written
     rationale) before the work is considered complete. Minor findings may
     be deferred, but are recorded, not silently dropped.
5. **No merge without a full, currently-green test run.** Before any branch
   is merged, the complete test suite is run against the merge result
   itself, not just against the pre-merge branch — a merge is not
   considered complete until this passes.
6. **Security architecture review as part of the design phase**, not a
   separate gate: every design/spec explicitly documents its security
   posture where relevant (e.g. what data a new feature does and does not
   persist, whether it introduces any new network surface, what user role
   can access it). For this project specifically, "does this introduce a
   network-facing component" is a standing question in every design
   review, because the platform's core security property is that it is
   fully offline and air-gapped by default — any change that would
   introduce network exposure is treated as an explicit, deliberate
   architectural decision requiring sign-off, not something that happens
   as a side effect of a feature.

## 2. Threat Modelling (Lightweight, Per-Change)

There is no separate formal threat-modelling document produced per change
at this project's current scale. Instead, every design/spec is required to
answer, as part of its own write-up:

- What new data does this store, and does it include anything sensitive
  (credentials, personal data)? If so, is it encrypted at rest, and is it
  excluded from anywhere it shouldn't propagate (e.g. a reusable template
  must never carry a stored SFTP password)?
- What new code paths does this add, and could any of them be reached with
  attacker-influenced input (a malformed file, a crafted config value, a
  hostile barcode payload)? If so, what happens on that path — does it fail
  safely, or can it corrupt state or crash the application?
- Does this change the application's role-based access control surface, and
  if so, is the new capability correctly gated to the roles that should
  have it?

This is deliberately lightweight and folded into the existing design-review
step rather than a separate formal artifact, appropriate to the project's
current scale (a single-developer-plus-AI-agent codebase, not a large team).
As the project grows — particularly if network-facing components are added
— this should be formalized into a proper threat model per component.

## 3. Release Process

1. Full test suite passes.
2. The release build (PyInstaller, `docscanner.spec`) is produced and
   smoke-tested — the packaged executable is launched and confirmed to
   reach its login screen without error, on a machine not previously used
   for development of the change being released.
3. The installer (Inno Setup) is compiled and a silent-install
   (`/VERYSILENT /NORESTART`) is verified to complete without prompts.
4. Known outstanding items are documented (see `README.md`'s "Remaining
   before go-live" section) rather than left implicit.

**Not yet in place:** code signing for the release executable (procurement
item, tracked separately), and a formalized rollback procedure beyond the
existing backup/restore mechanism (which has been verified to restore a
consistent database and file store to a clean machine from an exported key,
but has not been exercised specifically as a *release rollback* drill).

## 4. Known Gaps in This Policy (Stated Explicitly, Not Hidden)

- No independent (non-developer) code review exists yet at the level a
  regulated client's security requirements may expect — reviews described
  above are automated/AI-assisted but not performed by a human security
  reviewer independent of the development process. See
  `docs/security/SECURITY_TESTING_SCOPE.md` for the plan to address this.
- No formal secure-coding training program or certification process exists
  for this project's contributors.
- This document itself has not yet been reviewed or approved by anyone
  outside the immediate development process that wrote it.
