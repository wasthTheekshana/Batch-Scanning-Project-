# 0001: Central archive of record with store-and-forward PCs

Status: Proposed (awaiting approval). Date: 2026-10-15.
Design: `docs/superpowers/specs/2026-10-15-central-document-sync-design.md`.

## Context

Until now each scanning PC kept its documents and index data locally and the central service only distributed
project configuration. The product owner decided that finished documents and index data must also be synchronised
to the central server, with 10 or more PCs, unknown volume, and the most secure option. The earlier SRS and HLD
said "no server" and "fully offline". NFR-6 (5-10 stations per server) could not be met by a per-PC database.

## Decision

1. The central server becomes the **archive of record and the search point**. PCs remain autonomous working
   copies: scanning, indexing and delivery never wait for the network.
2. PCs send finished documents and index revisions through a **local outbox** (store and forward) over HTTPS with
   the existing per-PC key, using a resumable, checksum-verified, idempotent upload protocol.
3. Documents and index fields are **encrypted at rest on the server** with envelope encryption (AES-256-GCM per
   file, data keys wrapped by a master key kept outside the database, pluggable key provider). Identifier search
   uses blind indexes, not plaintext columns.
4. Index changes are **append-only revisions**; a concurrent edit is resolved by latest edit wins, with both
   kept and the document flagged for review.
5. Access is by role and project (admin, auditor, searcher), documents are streamed through the API only, and every
   view, download and search is logged.
6. The first document store is a directory or NAS share behind a `DocumentStore` interface; an S3-compatible store
   can be added without other changes.

## Consequences

- Bank documents now leave the PC. The central server becomes a high-value target: it needs TLS, mutual TLS and an
  IP allow-list (recommended), disk encryption, backups of both the store and the database, and its own threat
  model update (the existing one states central never receives documents) and a new SAST/DAST pass.
- The PC database needs a versioned schema (new `sync_outbox` table), so the migration mechanism (gap C6) must be
  built first. Central needs an Alembic baseline.
- Storage and backup capacity must be sized from real volume (design section 8 gives the formula).
- The desktop Search stays local and works offline; a fleet-wide search is a central feature only.
- SRS v2.0 FR-35 to FR-38 and UC-10 are the requirements this ADR serves; they stay "Planned" until built.

## Alternatives considered

- **Shared file server only (PCs write PDFs to a share):** simple, but no verified delivery, no index sync, no
  central search, no per-PC revocation. Rejected.
- **One central processing server, thin PCs:** meets NFR-6 literally but needs scanners streaming images to a
  server, breaks offline scanning, and is a rewrite. Rejected for now.
- **Database-level (TDE) encryption only:** protects a stolen disk but not a stolen database dump or a curious
  administrator. Kept as an extra layer, not the only one.
- **Last-writer-wins without history:** loses edits silently. Rejected in favour of revisions.
