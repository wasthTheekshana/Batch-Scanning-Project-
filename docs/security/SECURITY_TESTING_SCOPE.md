# Security Testing Scope Statement — DOK Scanner

**Status:** Draft.
**Date:** 2026-08-29

Defines which security testing disciplines apply to which component of DOK
Scanner, and on what cadence, per Enhancement Plan §7's requirement for "a
defined testing scope statement listing which of SAST/DAST/infra
assessments apply to which component, and on what cadence."

## Components in scope

| Component | Description |
|---|---|
| Core application (`core/`, `data/`, `imaging/`, `ocr/`, `qc/`, `separation/`, `export/`, `delivery/`, `scanning/`, `ui/`) | The Windows desktop application — the entire product surface today |
| Packaging (`docscanner.spec`, installer) | The build/distribution pipeline that turns source into a shipped executable |
| Custody module (`custody/`) | Physical bundle tracking, added this session |
| Templates module (`core/template_manager.py`) | Project-template system, added this session |

**Not yet in scope, because they don't exist yet:** any network-facing API,
web dashboard, or multi-site aggregation service — these are deferred per
`docs/ENHANCEMENT_PLAN_STATUS.md` (§3.1, §3.3, §3.7). Testing scope for
those components will be defined when and if they are built; DAST and
infrastructure vulnerability assessment do not meaningfully apply to a
component that never accepts a network connection.

## Testing disciplines and cadence

### Static Application Security Testing (SAST)

**Applies to:** the entire Python codebase (core application + custody +
templates modules).
**Tool:** Bandit (Python-specific static analyzer). A commercial/broader
tool (Semgrep, SonarQube) may be added later for deeper coverage; Bandit was
chosen for this first pass because it requires no network access to run
(consistent with this project's air-gapped operating principle) and needs
no paid tooling to start producing real findings immediately.
**Cadence:** run against every feature branch before merge to `main`, in
addition to the initial baseline scan recorded in
`docs/security/SAST_FINDINGS_REPORT.md`. Currently a manual step, not yet
wired into an automated CI gate (there is no CI pipeline for this project
yet — see "Known gaps" below).
**Independence:** the initial scan (this pass) was run and triaged by the
same process that writes the code. This does not satisfy an independence
requirement — see "Known gaps."

### Dynamic Application Security Testing (DAST)

**Applies to:** any network-facing component. **None currently exist.**
**Cadence:** first run required once §3.7 (API/Integration) or any other
network-facing component is built — not applicable before then.
**Tool:** not yet selected; OWASP ZAP is the leading candidate given it's
free, well-established, and (like Bandit) doesn't require a paid license to
start.

### Infrastructure Vulnerability Assessment

**Applies to:** any server/network infrastructure hosting a
consolidation/aggregation service. **Updated 2026-10-17:** a server component now
exists: the central service (`central/`, deployed with Docker Compose, PostgreSQL and a TLS
proxy per `central/deploy/DEPLOY.md`). It holds project settings and PC enrolments, no
documents. Its application and proxy behaviour are tested (`tests/central/`,
`scripts/check_tls.py`), but **no infrastructure vulnerability assessment of a real deployment
(host, database, proxy) has been run yet: it is due before go-live.** Document and index
consolidation across sites (§3.3 Multi-Site Consolidation) is still deferred.
**Cadence:** first run required before the central service is used with real customer data.

### Security Logging and Monitoring

**Applies to:** the application's existing audit trail (`audit_log` table,
`logs/audit.log` file).
**Current state:** already implemented and already structured. Every
audited action is written both to the encrypted database and to a
rotating log file, one JSON object per line — e.g.:

```json
{"timestamp": "2026-07-19T10:28:39.304+00:00", "action": "qc_review", "user": "admin", "document_id": "275b1ed5-...", "detail": {"decision": "accept"}}
```

This is already an ingestible, structured format (JSON Lines) suitable for
feeding a log-management system or SIEM without a translation step — it was
not built for this exercise, it's the format the audit logger has used
since Phase 2. No further work is needed to satisfy this line item beyond
confirming the format with whichever SIEM/log platform a client's security
team actually uses (CEF/Syslog conversion, if required by a specific
platform, is a small mapping step from this existing JSON, not a redesign).
**Cadence:** continuous — this is a running, real-time property of the
application, not a periodic test.

## Known gaps in this testing program

- **No CI pipeline.** All testing described above (including the SAST scan
  this document accompanies) is run manually by whoever is developing a
  change, not automatically on every push. Setting up automated CI (running
  the full test suite plus a SAST scan on every branch) is a reasonable
  next step but has not been done.
- **No independent tester.** Every test described here has been designed,
  run, and triaged by the same development process. A regulated client's
  security requirements will likely require this to be performed, or at
  minimum independently verified, by a separate internal QA/security
  function or an external tester — this program does not yet have one.
- **No DAST/infrastructure assessment has ever been run**, because no
  component in scope for either currently exists.
- **This scope statement itself has not been reviewed or approved by anyone
  independent of the process that wrote it.**
