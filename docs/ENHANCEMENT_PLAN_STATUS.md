# Enhancement Plan Status

Tracks implementation status against `docs/DOK_Scanner_Enhancement_Plan (1).docx`.
Last updated: 2026-10-09.

## Done (4 of 16 items)

| § | Item | What shipped |
|---|---|---|
| 3.2 | Chain-of-Custody Tracking | QR-coded bundle sheets, auto-logged scan checkpoint, manual receipt/prep/QC/storage/return checkpoints, reconciliation view, escalation hook for damaged/missing/compromised bundles |
| 4.3 | QC Pipeline Documentation & Coverage | One-click Strict/Standard/Lenient threshold presets; DPI WARN/FAIL thresholds now editable in the Workflow Designer (previously JSON-only); the "sample QC report format" ask was already covered by the existing Reports tab |
| 4.4 | Metadata Field Templates | Generalized beyond "banking only" per project direction — 5 built-in templates (Banking/Insurance/HR/Legal/Invoices) + admin "Save as template" so the library grows from real usage |
| 4.6 | Reporting Output | Explicit rejection-rate/rescan-rate metrics — required untangling the fact that "reject" and "rescan" QC decisions silently write the same `validation_status` value in the DB |

## Multi-PC central service (decided 2026-10-04: more than one scanning PC at go-live)

Spec `docs/superpowers/specs/2026-08-29-central-admin-dashboard-design.md` is now the active build (un-defers §3.3/§3.7 Phase 1). Three plans:

| Plan | Status |
|---|---|
| 1. Central backend API (`central/`) | **Built** on branch `central-api` (not yet merged): admin login/CSRF/lockout, PC enrollment (one-time codes), Bearer-key PC auth, versioned project/template config with credential scanning, push/assign, config pull, audit, threat model + SAST triage. |
| 2. Sync client in DOKScanner.exe | **Built** on branch `central-sync` (state, HTTPS client, config overlay, safe paths, sync service/worker, startup sync, Admin > Central tab, Workflow Designer limited to scanner/export/delivery for managed projects). End-to-end tests against the real central app: `tests/test_central_integration.py`. PC-local `scanner`/`export`/`delivery` stay local, are seeded from the PC's previous project and are editable in the Designer. |
| 3. React admin dashboard | Not started — needs admin password reset/disable, session revoke-all, PC deletion and an audit-log read API first (listed in `central/README.md`). |

Known limitations of plan 2 (updated 2026-10-12, per-project pipeline):

1. ~~Changes apply on restart.~~ **REMOVED.** The pipeline resolves each batch's
   project from the database when the batch starts, so a synced or
   Designer-saved change applies to the next scan batch (no restart notice).
2. ~~The scan wizard's project choice is not applied to the pipeline.~~
   **FIXED.** The wizard's project drives that batch's settings, export,
   delivery and filing (`tests/test_wizard_project_e2e.py`).

Remaining follow-ups of the per-project pipeline:

- Index tab zones / OCR mode per document project (still uses the startup project).
- CD/DVD burn tab per project (still the startup project's channel). Only the startup project's disc queue can be burned in the Admin tab; do not enable CD/DVD delivery on other projects yet. A
  non-startup project with CD/DVD enabled stages into its own `staging_dir`, and
  those documents stay `pending` with no screen to burn them.
- Duplicate detection compares across projects: an identical page scanned into
  two projects is held for review (deliberately global).
- The Index tab still uses the first project's zones / OCR mode.
- The scan wizard closes after a refused batch and its staged copies stay under
  `staging_dir` (no clean-up of refused batches yet).
- A dedicated exception type for batch refusal (today `ProjectUnavailable` and
  `ValueError` are both caught in `MainWindow.start_batch`).

## Remaining — grouped by why it's not done

### Group A — explicitly deferred by project decision
- §3.1 Mobile / On-Site Scanning Client
- §3.3 Multi-Site Consolidation & Central Reporting Layer

### Group B — blocked because of Group A
(the plan itself describes these as extensions of 3.1/3.3)
- §3.4 Secure Inter-Site Transfer Mechanism — needs sites to transfer *between*
- §3.8 Near-Real-Time Upload — needs 3.3's aggregation point to upload *to*
- §3.9 Remote Quality Monitoring — needs 3.3's dashboard to feed
- the "location/site as a report dimension" half of §4.6 — needs multiple sites to dimension by

### Group C — blocked on a decision only the business/client can make, not code
- §3.5 ECM / IBM FileNet-Compatible Export Mapping — needs a real bank's actual field schema; anything built without it is a guess
- §3.6 Incident & Insurance-Linked Security Workflow — needs a business decision on escalation policy (who gets notified, how fast) before there's a workflow to encode
- §4.7 Security Hardening for a Regulated Financial Client — the plan itself frames this as an SDLC policy document + SAST/DAST scan runs + a SOC-coverage staffing decision, not a feature to implement

### Group D — genuinely bigger/riskier, not started
- §3.7 API / Integration Capability — a new network-facing server; same category of architectural decision as §3.3 (already deferred)
- §4.1 OCR Engine Accuracy — a benchmarking *evaluation* (PaddleOCR/Surya vs. current engine on real documents), not a feature; the plan itself schedules this "before the Pilot," not now
- §4.2 Horizontal Scalability for 15M+ Pages — a real architecture change to the pipeline's execution model (worker-pool redesign), riskier than anything built so far

### Group E — not code at all
- §7 Security Evidence & Testing Readiness (SDLC policy doc, SAST/DAST scans, SOC coverage decision) — the plan explicitly warns not to claim any of this exists until it's actually produced; every row in its own tracking table is still "GAP"

## The challenges, stated plainly

1. **Groups A/B (7 of 12 remaining items) all trace back to one open question:** does this system get a network-facing / multi-machine architecture at all right now? Everything downstream of Mobile Client and Multi-Site Consolidation is on hold because that root question hasn't been decided — not because the individual features are hard.
2. **Group C's items need business/client input, not more engineering time** — a bank's real schema, an escalation policy, a SOC staffing call. These can't be productively guessed at.
3. **Group D's two code items are real architecture work**, not incremental additions like the last four shipped — they deserve the same brainstorm → design → plan → build rigor as chain-of-custody/templates, not a quick pass.
4. **Group E is explicitly process/paperwork**, and the plan itself warns against faking it — it needs to actually be produced (a written SDLC doc, a real Semgrep/ZAP scan run, a real staffing decision), which is a different kind of work entirely.

**Net:** everything easy and unblocked is done. What's left needs either a decision (Groups A-C), a bigger separately-scoped push (Group D), or isn't code at all (Group E).

## Known pain points (not from the enhancement plan, found during this work)

1. **Vendor scanner SDKs are stubs.** Kodak/Alaris/CZUR/Viisan degrade gracefully but don't actually scan until real vendor DLLs arrive — blocked on vendor relationships, not code.
2. **No code-signing cert** — the installer will trip Windows SmartScreen on a client machine until procured.
3. **Test suite has gotten slow and occasionally flaky.** Full runs now take 8-9 minutes (up from ~50s earlier) under load, and `test_crash_recovery_requeues_jobs` has intermittently failed under contention — a real timing fragility in the threaded-pipeline tests, not yet root-caused.
4. **Recurring test-hygiene bug:** some test writes into the tracked file `Routed/batch-1_0001.pdf` instead of a tmp dir, dirtying the working tree on every full-suite run. Manually reverted each time rather than fixed at the source.
5. **Client UAT hasn't happened.** Everything verified solo on one dev machine — no real scanner hardware, no real operator, no client sign-off yet.
6. ~~**The "unified audit trail" story has a gap.**~~ **Resolved 2026-10-04:** `custody/timeline.py` merges custody checkpoints and processing audit rows into one chronological view per bundle/batch/document (Custody tab timeline + CSV/PDF export from Custody and Reports tabs). Read-only; no schema change.
7. **`docs/SDLC.md` exists in the working tree but has not been authored or reviewed as part of this work** — if it's meant to answer §4.7's SDLC-policy ask, it needs a read before anyone relies on it.
