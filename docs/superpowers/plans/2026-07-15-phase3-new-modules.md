# DOK Scanner — Phase 3 New Modules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver Phase 3 of DOK_HLD_v2 (§18, weeks 8–13): real SFTP delivery, CD/DVD burn module, multi-channel delivery with rule triggers, performance benchmark suite, and a packaged EXE build path.

**Architecture:** Only the stub `deliver()` bodies change (HLD §12.2 stub pattern) — the channel classes, registry, DB schema, and delivery worker are untouched from Phase 1. Multi-channel adds rule-triggered channels to the existing worker loop. Benchmarks are a standalone script reusing the real pipeline.

**Tech Stack additions:** paramiko 5.x (LAN SFTP) · configurable burner CLI (ImgBurn/cdrecord) via subprocess · PyInstaller 6.x.

## Global Constraints

- LAN SFTP only; no internet. All tests offline — paramiko is monkeypatched, the burner CLI is faked with a local executable.
- Retry: failed deliveries retry 3× with backoff before flagging (HLD §19 reliability).
- Checksum: MD5 recorded for every delivery; CD/DVD burn verified against the staged copy after burn (HLD §19 success criteria).
- Channel `deliver()` must never raise — always return DeliveryResult.

## Tasks

### Task 1: SFTP delivery (`delivery/sftp_channel.py`)
- Config: `{enabled, host, port=22, username, password | key_file, remote_path, timeout=10}`.
- `is_available()` → enabled + paramiko importable + host/username present (no network probe).
- `deliver()`: connect (password or key), mkdir -p remote path, upload, verify remote size == local size, MD5 in result; 3 attempts with 1s backoff; audit-friendly detail. All exceptions → `DeliveryResult(success=False, detail={error})`.
- Tests: fake paramiko Transport/SFTPClient via monkeypatch — success, size-mismatch failure, connect error retries then fails, is_available gating.

### Task 2: CD/DVD burn module (`delivery/cddvd_channel.py`, `delivery/burn_queue.py`)
- `BurnQueue(staging_dir)`: documents accumulate in a burn session folder; `manifest.json` with per-file MD5; `total_size()`; `clear()`.
- `CDDVDChannel` config: `{enabled, burner_cmd: template with {source_dir} and {label}, staging_dir, disc_capacity_mb=4400}`; `deliver()` stages the file + checksum into the queue (status `burned_to_disc` only after an actual burn); `burn(label)` runs the CLI, then verifies every staged file's MD5 against the manifest; disc log appended to `burn_log.json`.
- Tests: fake burner command (python script copying staging → "disc" dir) — full burn + verify; corrupted disc file → failure; capacity check; deliver stages and manifest grows.

### Task 3: Multi-channel delivery (`core/job_queue.py`)
- Delivery worker: rule action `trigger_delivery` adds channels for this document — worker delivers to every enabled+available channel PLUS rule-triggered ones (by name), collects per-channel detail, sets `delivery_status` to the first successful channel's status value and `delivery_detail` per channel (already structured).
- Retry wrapper: each channel call retried 3× on failure.
- Tests: two fake channels — both delivered, detail has both; rule-triggered channel included; failing channel retries 3× and is recorded failed while other succeeds.

### Task 4: Benchmark suite (`scripts/benchmark.py`, smoke test)
- Generates N synthetic pages, runs the real pipeline (no OCR / OCR optional), reports pages/minute and pages/hour per stage and end-to-end; asserts nothing — prints a table; `--pages`, `--ocr` flags.
- `tests/test_benchmark.py`: 20-page run completes and yields > 0 pages/min (smoke, no absolute target in CI).

### Task 5: Packaging (`docscanner.spec` build)
- `pyinstaller docscanner.spec` build attempt; document result + bundle notes in README (tesseract.exe/tessdata/libzbar to be added for release).

## Verification
- Full pytest suite green; benchmark script prints throughput on this machine; PyInstaller build produces dist/DOKScanner.exe (or documented blocker).
