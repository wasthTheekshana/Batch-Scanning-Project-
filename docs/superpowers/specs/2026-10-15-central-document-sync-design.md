# Central document and index synchronisation: design

Status: DRAFT for approval, scope narrowed 2026-10-15 (see section 1). Date: 2026-10-15. Implements SRS v2.0 FR-35 to FR-38, UC-10, NFR-6 and NFR-8
(transport security). Decision record: `docs/adr/0001-central-document-and-index-sync.md`.
Gap list entry: `docs/GAP_ANALYSIS.md` B2.

## 1. Decisions taken (from the product owner, 2026-10-15)

| Question | Answer |
|---|---|
| What is the central copy? | The most secure option. Decided: central is the **archive of record and the search point**; each PC stays a working copy that keeps scanning on its own. |
| Does a PC keep working when the server is unreachable? | Yes (store and forward). |
| Storage of PDF files | **Paused.** The first release synchronises the **index data and the PDF's details** (name, size, SHA-256, page count, where it was delivered, delivery status) but **not the PDF bytes**. The upload protocol for the bytes (section 5, steps 1-3) and the document store (7.2) are designed here and built later, without changing the index sync. |
| Volume | Not known. 10 or more PCs. The design must scale by configuration; sizing figures in section 8 are assumptions to be replaced. |

## 1a. What changes with "index data only first"

- Central holds, for every document: the index fields (all revisions), the PDF's details and a `file_state`
  of `metadata_only`. When storage is added the state moves to `stored` after the bytes arrive and the SHA-256
  matches the value already on record, so nothing needs re-indexing.
- Central search works on index data and shows where the PDF is (the delivered folder or share and file name,
  or the PC that holds it). "Open PDF" is not available centrally until storage exists; the desktop Search tab
  still opens PDFs on the PC.
- PDF files must not be deleted from a PC's delivered folder by this system, and the SHA-256 on record is what
  later proves a stored copy is the right file.
- All index fields are personal data and are encrypted at rest on the server exactly as designed (7.3); the
  blind-index search and the roles apply from the first release.

## 2. Goals and non-goals

Goals: every finished document (PDF) and its index data reaches the central server intact, once, in order;
scanning never waits for the network; the server can search and open any document for authorised users; every
step is auditable; documents stay protected in transit and at rest.

Non-goals (this design): the central server does not run OCR, QC or scanning; no mobile client; no
public-internet exposure; no replacement of PC-side delivery (folder, SFTP, CD/DVD stay as they are).

## 3. Overview

```
 Scanning PC (Windows)                               Central server (customer network)
 pipeline -> finished document                       reverse proxy (TLS 1.2+, size cap, rate limit)
   |                                                    |
   +-> local DB + PDF + index file                      v
   +-> sync_outbox (SQLite, encrypted)   HTTPS     central API (FastAPI)
         |  sync worker (background) ---------------> upload / index / search / audit
         |  retry, backoff, resume                       |            |
         |                                               v            v
         +-- marks "synced" on server confirmation   Postgres      Document store
                                                      (index, sync  (encrypted PDFs;
                                                       state, audit) disk/NAS or S3-compatible)
```

## 4. PC side

### 4.1 Outbox

New table `sync_outbox` in the PC database (schema version bump; needs the migration mechanism from gap C6,
which is built first):

| Column | Meaning |
|---|---|
| `id` | autoincrement, gives the send order |
| `kind` | `document_meta` (the PDF's details), `index`, later `document_file`, `audit`, `custody` |
| `document_id` | the document this item belongs to |
| `revision` | for `index`: the index revision number (see 6) |
| `sha256`, `size` | of the PDF, for `document_meta` and later `document_file` items |
| `state` | `pending`, `sending`, `sent`, `failed_permanent` |
| `attempts`, `next_try_at`, `last_error` | retry bookkeeping (error text never contains document data) |

An item is written in the **same database transaction** that marks the document finished or its index changed, so
a crash can never lose or duplicate an upload.

### 4.2 Sync worker

Extends the existing `core/central_sync.py` worker. Runs in the background; sends items in `id` order, a
`document_meta` item always before the `index` items of that document. At most 2 uploads at a time per PC.
Failure handling: network error or 5xx retries with exponential backoff and jitter (1 min up to 1 h);
401/403 stops the worker and shows "re-enrol this PC" (key revoked); a checksum mismatch re-uploads;
a 4xx validation failure marks the item `failed_permanent`, raises an audit entry and a visible warning.
Scanning, indexing and delivery never depend on this worker.

### 4.3 Visibility

Admin > Central shows: items waiting, oldest waiting age, last success, last error. The Job Monitor shows a small
badge. A warning appears when the oldest item is older than a configurable time (default 4 hours).
Local copies are kept until the server has confirmed the document (and for the project's local retention days
after that).

## 5. Upload protocol (PC to server)

**First release (index data only):** step 0 below and step 4. **Deferred until storage is approved:** steps 1 to 3.

0. `PUT /pcs/{pc_id}/documents/{document_id}/meta` with `{project_id, batch_id, page_count, created_at, file_name,
   size, sha256, delivered_to, delivery_status}`. Idempotent: the same `document_id` and `sha256` is a no-op; a
   changed `sha256` (the PDF was re-exported after a correction) creates a new file record and keeps the old one.

All calls: HTTPS only, `Authorization: Bearer <per-PC key>` (existing `require_pc`), JSON or binary body, request
and response size caps. Every call is idempotent.

1. `POST /pcs/{pc_id}/documents` with `{document_id, project_id, batch_id, size, sha256, page_count, created_at}`.
   Server validates (UUID format, size cap, project assigned to this PC, the `document_meta` already on record
   with the same `sha256`) and replies with `upload_id` and the
   next expected offset. If the same `document_id` with the same `sha256` is already complete it replies
   `already_stored` (so a retry after a lost reply is harmless).
2. `PUT /pcs/{pc_id}/documents/{document_id}/chunks?offset=N` with up to 4 MiB of bytes. The server accepts only
   the expected offset, so an interrupted upload resumes where it stopped.
3. `POST /pcs/{pc_id}/documents/{document_id}/complete`. The server recomputes SHA-256 over the stored bytes,
   checks it equals the declared value and that the content is a PDF (`%PDF` header), encrypts and stores it, and
   replies `stored`. Only then does the PC mark the item `sent`.
4. `PUT /pcs/{pc_id}/documents/{document_id}/index` with `{revision, base_revision, edited_at, edited_by,
   fields, validation_status, project_version}`. Accepted if `revision` is new; a repeat of the same revision
   returns the stored result.

The server never trusts client-supplied file names or paths: the storage key is generated by the server.

## 6. Index data and corrections (FR-38)

Every index change (the automatic first read, an operator correction on a PC, a correction made centrally) is a
numbered **revision** of the document's index, append-only on the server.

- A writer sends `base_revision` (the revision its edit started from).
- If `base_revision` equals the server's current revision the edit simply becomes the next revision.
- If not, two people edited from the same starting point: the **latest `edited_at` wins** as current, the other
  is kept as a non-current revision, and the document is flagged `conflict` so a supervisor can review it. Both
  edits stay in the audit trail. (Clocks: the server stamps `received_at`; `edited_at` is used only to order two
  edits and PCs must keep their clocks within a minute, checked at each sync.)
- A correction made centrally comes back to the owning PC on its next sync (`GET /pcs/{pc_id}/index-updates?since=`),
  which rewrites the local index and the delivered index file (existing "reissue after correction" logic).

## 7. Server side

### 7.1 Data model (new tables; Alembic migration, baseline first)

| Table | Key columns |
|---|---|
| `documents` | `id` (server UUID), `pc_id`, `source_document_id`, `project_id`, `batch_id`, `page_count`, `created_at`, `received_at`, `current_revision`, `conflict` (bool), `deleted_at`. Unique `(pc_id, source_document_id)`. |
| `document_files` | `document_id`, `file_name`, `size`, `sha256`, `delivered_to`, `file_state` (`metadata_only` / `stored`), and, once storage exists, `storage_key`, `wrapped_data_key`, `nonce`, `key_version` |
| `index_revisions` | `document_id`, `revision`, `base_revision`, `fields_enc`, `edited_by`, `edited_at`, `received_at`, `source` (`pc`/`central`), `is_current` |
| `index_lookup` | `document_id`, `field_name`, `blind_hash` (see 7.3), indexed |
| `upload_sessions` | `document_id`, `pc_id`, `expected_offset`, `size`, `sha256`, `expires_at` |
| `document_access_log` | `at`, `user`, `document_id`, `action` (`search`, `view`, `download`, `export`), `ip` |
| `retention_holds` | `document_id` or `project_id`, `reason`, `set_by` |

The existing `audit_events` table also records every upload, completion, failure, conflict and deletion.

### 7.2 Document store (deferred; not part of the first release)

An interface `DocumentStore` with `put`, `get_stream`, `delete`, `exists`. First implementation: a directory on
a server disk or NAS share the customer provides (no new infrastructure). A second implementation for an
S3-compatible store can be added later without touching the rest. Files are written to a temp name, flushed, then
renamed, so a half-written file is never visible.

### 7.3 Encryption at rest (decision)

- **PDFs:** envelope encryption. Each file gets a random 256-bit data key and is encrypted with AES-256-GCM.
  The data key is wrapped by a master key (KEK) and stored in `document_files`. The KEK is **not** stored in
  the database or next to the files: it is read from a key file with restricted permissions, a Windows DPAPI
  machine key, or the customer's KMS or HSM if they have one (pluggable `KeyProvider`). Rotating the KEK only
  re-wraps data keys; it does not re-encrypt files. `key_version` records which KEK wrapped each key.
- **Index fields:** stored encrypted (`fields_enc`) with the same scheme so a database dump alone reveals no NICs
  or account numbers.
- **Search:** exact-match lookup on identifier fields (NIC, account number, customer ID, as chosen per project)
  uses a **blind index**: an HMAC-SHA256 of the normalised value with a separate search key, stored in
  `index_lookup`. The server can find a document by an identifier without a plaintext column. Prefix and
  free-text search over identifier fields is deliberately not offered; date, batch and project filters use
  plain columns.
- Disk or volume encryption (BitLocker or LUKS) on the server is still required; this scheme protects against a
  copied database or copied file share, not a fully compromised running server.

### 7.4 Access control and auditing

- New central roles (today only `admin`): `admin`, `auditor` (read only, sees audit and documents, cannot change
  config), `searcher` (search and open documents of assigned projects only). Scoping by project.
- Documents are streamed through the API after an authorisation check, never from a static URL. Every search,
  view, download and export writes `document_access_log`. Optionally a visible watermark with the user name is
  added to viewed PDFs (project setting).
- Sessions and lockout as today (server-side, 8 h, CSRF); new roles reuse them.
- PC keys stay per PC and revocable. Recommended hardening for the bank: **mutual TLS** at the reverse proxy
  with one client certificate per PC in addition to the key, and an IP allow-list for the upload endpoints.
- Request caps at the proxy: 4 MiB chunks, 1 MiB for JSON; rate limit per PC; the application rejects anything
  larger.

### 7.5 Search API and UI

`GET /admin/documents?customer_id=&nic=&account_no=&date_from=&date_to=&project=&pc=&batch=&status=` (identifier
filters via blind index), paged. `GET /admin/documents/{id}` (metadata and current index), `.../file` (stream),
`.../history` (revisions and conflicts). The existing dashboard gets a Search page and a Document page. The
desktop Search tab stays local to the PC (fast, works offline); a later option can add a "search all PCs" button
when online.

### 7.6 Retention and deletion

Per-project retention period (days) set by the customer. A scheduled job deletes expired documents that are not
on hold: file removed, index revisions removed or reduced to a tombstone (document id, deleted_at, reason), audit
entry written. PCs receive a tombstone on sync so their local copy and delivered index entry can be purged by
policy. Legal hold blocks deletion.

## 8. Sizing (assumptions, replace with real figures)

Assumptions: 10 PCs, 3,000 pages per PC per day, 80 KB per searchable-PDF page, 250 working days, 7-year
retention. Per day: 30,000 pages is about 2.4 GB. Per year about 600 GB, 7 years about 4.2 TB (plan about 5 TB
usable plus the same again for backup). Database: about 1 row per document (say 6,000 per day, 1.5 M per year),
trivial for Postgres. Network: 2.4 GB per day is a few Mbit/s averaged; a LAN is more than enough, and a slow WAN
simply makes the outbox longer. Scaling levers when volume is higher: more API workers, a bigger or object store,
per-PC upload concurrency, Postgres read replica for search. If a customer's volume is 10 times these figures
the design does not change; the storage and backup plan does.

## 9. Failure and edge cases

| Case | Behaviour |
|---|---|
| Server down for days | Outbox grows; scanning continues; on return items drain in order, oldest first. |
| Connection drops mid-file | Resume from the server's expected offset. |
| Reply lost after a successful store | Retry returns `already_stored`; no duplicate. |
| PC restored from backup | Outbox rows restored; server de-duplicates on `(pc_id, source_document_id, sha256)`. |
| Same page scanned twice on two PCs | Two documents; duplicate detection stays per PC (existing behaviour); central can report likely duplicates by hash later. |
| Disk full on the server | Upload returns 507; PC keeps retrying with backoff; admin alert in the dashboard. |
| PC key revoked | Uploads stop with a clear message; documents stay queued until the PC is re-enrolled. |
| Clock skew over 1 minute | Sync reports a warning; corrections ordered by server receive time as a fallback. |

## 10. Build phases

0. Prerequisites: schema-migration mechanism on the PC (gap C6); Alembic baseline for central; CI (gap C2).
1. **Index and details upload**: outbox, worker, `meta` and `index` calls, encrypted index storage, upload audit,
   status in the Central tab. Done when a document scanned on a PC while the server is down has its index record and PDF details on
   the server, exactly once, after the server returns.
2. **Search and access**: roles, blind-index search over index data, document page (details and index history), access log,
   dashboard pages.
3. **Corrections**: index revisions both ways, conflict handling, index updates back to PCs.
4. **Audit and custody sync**, retention and holds, key rotation tool, operations pack (HTTPS proxy sample, backup
   and restore of the store plus database, monitoring checklist).

Each phase gets its own plan under `docs/superpowers/plans/` and keeps the test-first rule. Tests: real central app
against a temp store (the `check_central.py` style end-to-end), failure injection for dropped chunks and lost
replies, a tampered-bytes test, a wrong-key test, and role-scoping tests.

5. **PDF storage** (paused): document store, envelope encryption of files, the chunked upload (steps 1 to 3 of
   section 5), "Open PDF" and download centrally, move `file_state` to `stored`. Needs the storage location and
   sizing answers in section 11.

## 11. Open points for the customer

1. Real volume (pages per day per PC, number of PCs, retention years).
2. Where the document store lives and who backs it up (a NAS path, or their S3-compatible store).
3. Whether the bank mandates its own KMS or HSM for the master key.
4. Who may search and open documents centrally, and whether viewing needs a watermark.
5. Whether mutual TLS and an IP allow-list are acceptable on their network.
6. Whether documents may ever be deleted before the retention period ends, and who can authorise it.
