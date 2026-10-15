"""Queue-based processing pipeline (HLD §7).

Six named worker stages connected by queue.Queue:

    ingest → OCR pool (×N) → enhancement → QC → rule engine → delivery

Page stages (ingest/OCR/enhance/QC) run per page. When the last page of
a batch clears QC, the batch is separated into documents, and each
document flows through the rule and delivery stages. No stage blocks
another; the UI thread is never involved.

Crash recovery: every ScanJob is persisted to the jobs table before
processing (status queued → in_progress → complete/failed). On startup,
recover() re-queues anything unfinished (HLD §14.2).
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import traceback
from pathlib import Path

import cv2

from core.models import PageRecord, ScanJob, new_id
from core.project_runtime import ProjectRuntime, ProjectUnavailable
from imaging.blank_detect import ink_density, is_blank
from imaging.crop import auto_crop
from imaging.deskew import deskew
from imaging.enhance import apply_profile
from qc.pipeline import run_qc
from export.index_export import (append_running_csv, build_record,
                                 write_index)
from ocr.doc_types import inherit_types, page_zone_groups
from ocr.orientation import turn, upright
from separation.barcode import decode_barcodes, filter_barcodes
from separation.rule_engine import RuleEngine, RuleSetError
from separation.splitter import split_documents

log = logging.getLogger(__name__)

_SENTINEL = object()

# A field's lookup table only starts rejecting unrecognised values once
# enough history exists to trust it — otherwise every project's first few
# documents would flag every field as unknown.
_LOOKUP_MIN_SAMPLES = 5

# Pipeline.can_resubmit reason for a document whose page images are gone
# (the one refusal the QC review still lets an approval through).
REASON_NO_PAGE_IMAGES = "page images unavailable"


class Pipeline:
    """Wires the six workers to a project profile.

    Collaborators are injectable for tests:
      ocr_fn(image) -> OCRResult-like (or None to skip OCR)
      export_fn(images, export_cfg, ctx, ocr_texts) -> Path
      channels: list[BaseDeliveryChannel]
    """

    def __init__(self, db, profile: dict, channels: list, export_fn,
                 audit=None, ocr_fn=None, ocr_workers: int = 2,
                 working_dir: str | Path = "staging",
                 trigger_pool: dict | None = None,
                 delivery_attempts: int = 3,
                 resolver=None):
        self.db = db
        self.profile = profile
        self.channels = channels
        # channels addressable by the trigger_delivery rule action, even
        # when not project-enabled (Phase 3 multi-channel delivery)
        self.trigger_pool = trigger_pool or {c.name: c for c in channels}
        self.resolver = resolver
        # the constructor's profile/channels/trigger_pool ARE the runtime when no
        # resolver is given (legacy behaviour); kept as attributes for compatibility
        self._default = ProjectRuntime(
            project_id=(profile or {}).get("project_id", ""),
            profile=profile, channels=channels, trigger_pool=self.trigger_pool)
        self.delivery_attempts = delivery_attempts
        self.export_fn = export_fn
        self.audit = audit
        self.ocr_fn = ocr_fn
        self.working_dir = Path(working_dir)
        self.working_dir.mkdir(parents=True, exist_ok=True)

        self.ingest_q: queue.Queue = queue.Queue()
        self.ocr_q: queue.Queue = queue.Queue()
        self.enhance_q: queue.Queue = queue.Queue()
        self.qc_q: queue.Queue = queue.Queue()
        self.rule_q: queue.Queue = queue.Queue()
        self.delivery_q: queue.Queue = queue.Queue()

        self._subscribers: list = []
        self._batches: dict[str, dict] = {}   # batch_id -> {expected, pages, counter}
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._ocr_workers = ocr_workers
        self._started = False
        # (batch_id, message) of every batch the last recover() refused;
        # messages are str(ProjectUnavailable) only (value-free)
        self.refused_batches: list[tuple[str, str]] = []

    # -- events ----------------------------------------------------------------
    def subscribe(self, fn) -> None:
        self._subscribers.append(fn)

    def _emit(self, stage: str, status: str, **data) -> None:
        event = {"stage": stage, "status": status, **data}
        for fn in list(self._subscribers):
            try:
                fn(event)
            except Exception:
                log.exception("event subscriber failed")

    def _resolve(self, project_id: str) -> ProjectRuntime:
        """The runtime for a project id. Without a resolver there is exactly
        one runtime (the constructor's). Raises ProjectUnavailable."""
        if self.resolver is None:
            return self._default
        return self.resolver.resolve(project_id)

    def _rt(self, batch_id: str, project_id: str | None = None) -> ProjectRuntime:
        """Settings for a batch: its start-of-batch snapshot if this session
        still holds it, otherwise resolved from the given project id (or the
        batch row's). The lock is not held while resolving."""
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

    # -- lifecycle ----------------------------------------------------------------
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        specs = [("ingest", self._ingest_worker, 1),
                 ("ocr", self._ocr_worker, self._ocr_workers),
                 ("enhance", self._enhance_worker, 1),
                 ("qc", self._qc_worker, 1),
                 ("rule", self._rule_worker, 1),
                 ("delivery", self._delivery_worker, 1)]
        for name, target, count in specs:
            for i in range(count):
                t = threading.Thread(target=target, name=f"{name}-{i}",
                                     daemon=True)
                t.start()
                self._threads.append(t)

    def shutdown(self, wait: bool = True) -> None:
        if not self._started:
            return
        self.ingest_q.put(_SENTINEL)
        for _ in range(self._ocr_workers):
            self.ocr_q.put(_SENTINEL)
        self.enhance_q.put(_SENTINEL)
        self.qc_q.put(_SENTINEL)
        self.rule_q.put(_SENTINEL)
        self.delivery_q.put(_SENTINEL)
        if wait:
            for t in self._threads:
                t.join(timeout=30)
        self._started = False
        self._threads.clear()

    # -- submission -----------------------------------------------------------------
    def submit_batch(self, batch_id: str, jobs: list[ScanJob],
                     operator: str = "system") -> None:
        """Persist and enqueue a whole scan batch."""
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
        # insert first: a DB error must not leave an in-memory entry behind
        self.db.insert_batch(batch_id, runtime.project_id, operator)
        with self._lock:
            self._batches[batch_id] = {"expected": len(jobs), "pages": [],
                                       "counter": 0, "operator": operator,
                                       "runtime": runtime}
        for job in jobs:
            self.db.upsert_job(job.job_id, job.batch_id, job.project_id,
                               job.page_no, "queued", job.image_path)
            self.ingest_q.put(job)

    def recover(self) -> list[ScanJob]:
        """Re-queue unfinished jobs from a previous run (HLD §14.2).
        Recovered jobs are grouped per batch and resubmitted. Batches that
        cannot be resumed are failed and listed in ``refused_batches``."""
        self.refused_batches = []
        rows = self.db.get_incomplete_jobs()
        by_batch: dict[str, list[ScanJob]] = {}
        for r in rows:
            job = ScanJob(batch_id=r["batch_id"], project_id=r["project_id"],
                          page_no=r["page_no"], image_path=r["payload_path"],
                          job_id=r["job_id"])
            by_batch.setdefault(job.batch_id, []).append(job)
        recovered = []
        for batch_id, jobs in by_batch.items():
            project_ids = {j.project_id for j in jobs}
            try:
                if self.resolver is not None and len(project_ids) != 1:
                    raise ProjectUnavailable("recovered batch spans several projects")
                runtime = self._resolve(next(iter(project_ids), ""))
            except ProjectUnavailable as e:
                # message only: the chained cause can contain config values
                log.warning("cannot recover batch %s: %s", batch_id, e)
                for job in jobs:
                    self.db.mark_job(job.job_id, "failed")
                self.db.update_batch_status(batch_id, "failed")
                self.refused_batches.append((batch_id, str(e)))
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
        if recovered:
            log.info("recovered %d unfinished jobs", len(recovered))
        return recovered

    # -- workers -------------------------------------------------------------------
    def _pump(self, q_in: queue.Queue, stage: str, handler) -> None:
        while True:
            item = q_in.get()
            if item is _SENTINEL:
                q_in.task_done()
                return
            try:
                handler(item)
            except Exception as e:
                if isinstance(e, ProjectUnavailable):
                    # message only: the chained cause can contain config values
                    log.error("stage %s failed: %s", stage, e)
                else:
                    log.error("stage %s failed: %s\n%s", stage, e,
                              traceback.format_exc())
                job = item.job if isinstance(item, PageRecord) else item
                if isinstance(job, ScanJob):
                    self.db.mark_job(job.job_id, "failed")
                self._emit(stage, "failed", error=str(e))
            finally:
                q_in.task_done()

    def _ingest_worker(self) -> None:
        def handle(job: ScanJob):
            self.db.mark_job(job.job_id, "in_progress")
            image = cv2.imread(job.image_path, cv2.IMREAD_UNCHANGED)
            if image is None:
                raise IOError(f"cannot read image {job.image_path}")
            record = PageRecord(job=job)
            rt = self._rt(job.batch_id, job.project_id)
            sep_cfg = rt.profile.get("separation", {})
            record.ink_density = ink_density(image)
            record.is_blank = is_blank(
                image, sep_cfg.get("blank_threshold", 0.02))
            # NOTE: the automatic custody "scanning" checkpoint (below) only
            # fires when barcode decoding runs, which requires "barcode" in
            # this project's separation.strategy. A project using only
            # blank_page/fixed_pages separation will not auto-log scanning
            # checkpoints for custody bundles even if bundles exist for it.
            if "barcode" in sep_cfg.get("strategy", []):
                decoded = decode_barcodes(image)
                # custody labels are matched against every barcode found;
                # only the project's own barcodes may separate documents
                record.barcodes = filter_barcodes(decoded, sep_cfg)
                if decoded:
                    from custody.bundle_manager import (
                        record_checkpoint_from_barcode)
                    with self._lock:
                        operator = self._batches.get(job.batch_id, {}).get(
                            "operator", "system")
                    for bc in decoded:
                        try:
                            matched = record_checkpoint_from_barcode(
                                self.db, bc["value"], "scanning", operator,
                                batch_id=job.batch_id, audit=self.audit)
                            if matched:
                                record.is_custody_label = True
                        except Exception:
                            log.exception(
                                "custody checkpoint failed for barcode %s on "
                                "job %s", bc.get("value"), job.job_id)
            self._emit("ingest", "done", job_id=job.job_id,
                       batch_id=job.batch_id, page_no=job.page_no)
            self.ocr_q.put(record)
        self._pump(self.ingest_q, "ingest", handle)

    def _ocr_worker(self) -> None:
        def handle(record: PageRecord):
            rt = self._rt(record.job.batch_id, record.job.project_id)
            # nothing to read on a page the project will drop anyway (a blank
            # separator, or an empty back when empty pages are discarded);
            # a merely sparse page (ID card, short note) is real content
            separation = rt.profile.get("separation", {})
            skip_ocr = ((record.is_blank
                         and "blank_page" in separation.get("strategy", []))
                        or (record.is_empty
                            and bool(separation.get("drop_blank_pages"))))
            if self.ocr_fn is not None and not skip_ocr:
                image = cv2.imread(record.job.image_path, cv2.IMREAD_UNCHANGED)
                result = self.ocr_fn(image)
                if not record.barcodes:        # a barcode page is known: leave it
                    # a page fed upside down or sideways reads as gibberish:
                    # read it the other ways up and keep the one that reads
                    record.rotation, image, result = upright(
                        image, result, self.ocr_fn)
                if result is not None:
                    record.ocr_text = result.text
                    record.ocr_confidence = result.confidence
                    record.ocr_words = list(getattr(result, "words", None) or [])
                self._extract_zone_fields(record, image, rt)
            self._emit("ocr", "done", job_id=record.job.job_id,
                       batch_id=record.job.batch_id)
            self.enhance_q.put(record)
        self._pump(self.ocr_q, "ocr", handle)

    def _extract_zone_fields(self, record: PageRecord, image, rt) -> None:
        """Auto-index: extract configured OCR zones into page fields
        (HLD §4 Layer 5). Values below the confidence threshold are kept
        but flagged so the operator can correct them in the index editor.
        ocr.mode selects zone-based extraction (fixed pixel regions,
        cropped and re-OCR'd) or full-page extraction (label-anchored
        regex against the page text already OCR'd for record.ocr_text)."""
        ocr_cfg = rt.profile.get("ocr", {})
        # a page's zones are the common ones plus those of its document type
        # (NIC, application, ...); the type also labels the page in the PDF,
        # and may read its zones differently from the rest of the project
        groups, record.doc_type = page_zone_groups(ocr_cfg, record.ocr_text)
        if not groups or not rt.profile.get("features", {}).get("auto_index"):
            return
        threshold = float(ocr_cfg.get("confidence_threshold", 0.75))
        fields: dict = {}
        for zones, mode in groups:
            try:
                if mode == "full_page":
                    from ocr.zone_extractor import extract_fields_from_text
                    fields.update(extract_fields_from_text(
                        record.ocr_text, record.ocr_confidence, zones))
                else:
                    from ocr.zone_extractor import extract_fields
                    fields.update(extract_fields(
                        image, zones, lang=ocr_cfg.get("lang", "eng"),
                        ocr_fn=self.ocr_fn, words=record.ocr_words))
            except Exception as e:
                log.warning("zone extraction failed for page %s: %s",
                            record.job.page_no, e)
        for name, fv in fields.items():
            record.extracted_fields[name] = fv.value
            if not fv.value or fv.confidence < threshold:
                record.low_conf_fields.append(name)
            elif self._fails_lookup(name, fv.value, rt.project_id):
                record.low_conf_fields.append(name)

    def _fails_lookup(self, field_name: str, value: str,
                      project_id: str | None = None) -> bool:
        """True if the value doesn't match this field's lookup table of
        previously-indexed values (HLD §4 Layer 5: "validated against
        lookup tables in SQLite"). Only enforced once the field has
        enough history to be a trustworthy lookup set. History is the
        project's own: another project's documents never open the gate."""
        known = self.db.known_field_values(field_name, project_id)
        return len(known) >= _LOOKUP_MIN_SAMPLES and value not in known

    def _enhance_worker(self) -> None:
        def handle(record: PageRecord):
            profile = self._rt(record.job.batch_id,
                               record.job.project_id).profile.get(
                "enhancement_profile", {})
            image = turn(cv2.imread(record.job.image_path, cv2.IMREAD_UNCHANGED),
                         record.rotation)
            if not record.is_empty:
                if profile.get("deskew"):
                    image, _ = deskew(image)
                if profile.get("crop"):
                    image = auto_crop(image)
                image = apply_profile(image, profile)
            working = self.working_dir / f"{record.job.job_id}_work.png"
            cv2.imwrite(str(working), image)
            record.working_path = str(working)
            self._emit("enhance", "done", job_id=record.job.job_id,
                       batch_id=record.job.batch_id)
            self.qc_q.put(record)
        self._pump(self.enhance_q, "enhance", handle)

    def _qc_worker(self) -> None:
        def handle(record: PageRecord):
            rt = self._rt(record.job.batch_id, record.job.project_id)
            qc_cfg = rt.profile.get("qc", {"enabled": False})
            if record.is_empty:
                record.qc_score, record.qc_grade = 100, "PASS"
            else:
                image = cv2.imread(record.working_path, cv2.IMREAD_UNCHANGED)
                fixed, verdict = run_qc(
                    image, qc_cfg,
                    {"dpi": rt.profile.get("scanner", {}).get("dpi", 300)})
                cv2.imwrite(record.working_path, fixed)
                record.qc_score = verdict.score
                record.qc_grade = verdict.grade
                record.qc_detail = {**verdict.detail, "flags": verdict.flags}
            # image paths ride along so the QC review UI can show
            # before/after and re-export after operator approval
            record.qc_detail = {**record.qc_detail,
                                "original_path": record.job.image_path,
                                "working_path": record.working_path,
                                "rotation": record.rotation,
                                "ocr_text": record.ocr_text}
            self.db.mark_job(record.job.job_id, "complete")
            self._emit("qc", "done", job_id=record.job.job_id,
                       batch_id=record.job.batch_id, grade=record.qc_grade,
                       score=record.qc_score)
            self._collect_page(record)
        self._pump(self.qc_q, "qc", handle)

    def _collect_page(self, record: PageRecord) -> None:
        """Gather QC-complete pages; when the batch is whole, separate
        into documents and push them to the rule stage."""
        batch_id = record.job.batch_id
        with self._lock:
            state = self._batches.get(batch_id)
            if state is None:
                return
            state["pages"].append(record)
            if len(state["pages"]) < state["expected"]:
                return
            pages = sorted(state["pages"], key=lambda r: r.job.page_no)

        page_dicts = [{"page_no": r.job.page_no, "image": None,
                       "is_blank": r.is_blank, "ink_density": r.ink_density,
                       "barcodes": r.barcodes,
                       "is_custody_label": r.is_custody_label,
                       "ocr_text": r.ocr_text,
                       "record": r} for r in pages]
        rt = self._rt(batch_id, record.job.project_id)
        docs = split_documents(page_dicts, rt.profile.get("separation", {}))
        self._audit_excluded_pages(batch_id, page_dicts, docs)
        self.db.update_batch_status(batch_id, "qc_pending", len(pages))
        self._emit("separation", "done", batch_id=batch_id,
                   documents=len(docs))
        for doc in docs:
            self.rule_q.put({"batch_id": batch_id, "doc": doc})

    def _audit_excluded_pages(self, batch_id: str, page_dicts: list,
                              docs: list) -> None:
        """Pages that ended up in no document (cover sheets, custody labels,
        discarded blank pages) are recorded, so a page is never dropped
        without a trace."""
        kept = {p["page_no"] for d in docs for p in d["pages"]}
        left_out = [p for p in page_dicts if p["page_no"] not in kept]
        if not left_out or self.audit is None:
            return
        reason = {}
        for p in left_out:
            reason[p["page_no"]] = ("custody_label" if p["is_custody_label"]
                                    else "separator" if p["barcodes"]
                                    else "blank")
        self.audit.log("pages_excluded", "system", batch_id=batch_id,
                       count=len(left_out), reasons=reason)

    def _rule_worker(self) -> None:
        def handle(item: dict):
            doc = item["doc"]
            rt = self._rt(item["batch_id"])
            records = [p["record"] for p in doc["pages"]]
            for r, name in zip(records, inherit_types(
                    [r.doc_type for r in records])):
                r.doc_type = name
            worst = min((r.qc_score for r in records), default=100)
            grades = {r.qc_grade for r in records}
            # auto-index: merge page zone fields (first non-empty wins)
            merged_fields: dict = {}
            low_conf: set[str] = set()
            for r in records:
                for name, value in r.extracted_fields.items():
                    if value and not merged_fields.get(name):
                        merged_fields[name] = value
                low_conf.update(r.low_conf_fields)

            document = {
                "document_id": new_id(),
                "batch_id": item["batch_id"],
                "project_id": rt.project_id,
                "barcode": doc.get("barcode"),
                "qc_score": worst,
                "validation_status": "auto_approved"
                if grades <= {"PASS"} else "pending_qc",
                "extracted_fields": merged_fields,
                "ocr_text": " ".join(r.ocr_text for r in records if r.ocr_text),
                "flags": sorted({f for r in records
                                 for f in r.qc_detail.get("flags", [])}),
            }
            if low_conf:
                document["flags"] = sorted(set(document["flags"])
                                           | {"low_confidence_index"})
            if doc.get("barcode"):
                document["extracted_fields"]["barcode"] = doc["barcode"]
            found_types = list(dict.fromkeys(
                r.doc_type for r in records if r.doc_type))
            if found_types:
                document["extracted_fields"]["doc_types"] = ", ".join(found_types)

            # duplicate detection: MD5 + pHash of first page, barcode
            # cross-check against previously processed documents
            fingerprint_detail = self._check_duplicate(document, records, rt)

            rules = rt.profile.get("rules") or {}
            if rules.get("rules"):
                try:
                    RuleEngine(rules).evaluate(document)
                except RuleSetError as e:
                    log.error("rule set invalid, skipping rules: %s", e)

            page_nos = [p["page_no"] for p in doc["pages"]]
            qc_detail = {"pages": {str(r.job.page_no): (
                {**r.qc_detail, "doc_type": r.doc_type} if r.doc_type
                else r.qc_detail) for r in records},
                         "flags": document["flags"]}
            if fingerprint_detail:
                qc_detail["fingerprint"] = fingerprint_detail
            self.db.insert_document(
                document["document_id"], item["batch_id"],
                rt.project_id,
                page_range=f"{min(page_nos)}-{max(page_nos)}",
                enhancement_profile="default",
                extracted_fields=document["extracted_fields"],
                validation_status=document["validation_status"],
                qc_score=worst,
                qc_detail=qc_detail,
                duplicate_of=document.get("duplicate_of"))
            if self.audit:
                self.audit.log("rule_engine", "system",
                               document_id=document["document_id"],
                               status=document["validation_status"])
            self._emit("rule", "done", document_id=document["document_id"],
                       validation_status=document["validation_status"])
            self.delivery_q.put({"document": document, "records": records})
        self._pump(self.rule_q, "rule", handle)

    def reissue_index(self, document_id: str) -> bool:
        """Write and deliver the index file again after an operator changed
        a document's fields. True when a re-issue was queued; False when
        there is nothing to do (no index file configured, document not
        exported, project unavailable). Runs on the delivery thread."""
        row = self.db.get_document(document_id)
        if row is None or not row["storage_path"] \
                or not Path(row["storage_path"]).is_file():
            return False
        try:
            rt = self._resolve(row["project_id"])
        except ProjectUnavailable:
            return False
        if not ((rt.profile.get("export") or {}).get("index") or {}).get("enabled"):
            return False
        self.delivery_q.put({"reissue_index": document_id})
        return True

    def _do_reissue_index(self, document_id: str) -> None:
        row = self.db.get_document(document_id)
        if row is None:
            return
        try:
            rt = self._resolve(row["project_id"])
        except ProjectUnavailable:
            return
        pages = json.loads(row["qc_detail"] or "{}").get("pages", {})
        index_path = self._write_index_file(
            document_id, row["storage_path"], rt,
            (rt.profile.get("export") or {}).get("index") or {},
            page_count=len(pages),
            fields=json.loads(row["extracted_fields"] or "{}"),
            qc_score=row["qc_score"])
        if index_path is None:
            return
        details = json.loads(row["delivery_detail"] or "{}")
        sent_to = []
        for channel in rt.channels:
            # only where the document already went; a disc queue would stage a
            # second copy, so it is left alone
            if channel.name == "cd_dvd" or not details.get(channel.name, {}).get("success"):
                continue
            sent = self._deliver_with_retry(
                channel, str(index_path),
                {"document_id": document_id, "project_id": row["project_id"]})
            details[channel.name]["index"] = {"success": sent.success,
                                              "checksum": sent.checksum}
            sent_to.append(channel.name)
        self.db.update_document(document_id, delivery_detail=details)
        if self.audit:
            self.audit.log("index_reissued", "system", document_id=document_id,
                           channels=sent_to)

    def _delivery_worker(self) -> None:
        def handle(item: dict):
            if "reissue_index" in item:
                self._do_reissue_index(item["reissue_index"])
                return
            document, records = item["document"], item["records"]
            doc_id = document["document_id"]

            if document["validation_status"] != "auto_approved":
                # WARN/FAIL documents wait in the review queue; the QC
                # review UI re-submits them for export after approval.
                self._emit("delivery", "held_for_review", document_id=doc_id)
                return

            rt = self._rt(document["batch_id"], document.get("project_id"))
            export_cfg = rt.profile.get("export", {})
            with self._lock:
                # setdefault: batch state may be gone after a restart
                # (e.g. review-approved documents from an old session)
                state = self._batches.setdefault(
                    document["batch_id"],
                    {"expected": 0, "pages": [], "counter": 0,
                     "operator": "review"})
                state["counter"] += 1
                counter = state["counter"]
            ctx = {"batch_id": document["batch_id"],
                   "counter": counter,
                   "barcode": document.get("barcode") or "",
                   "date": __import__("datetime").datetime.now().strftime("%Y%m%d"),
                   "fields": document["extracted_fields"],
                   "output_folder": document.get("output_folder", ""),
                   "dpi": rt.profile.get("scanner", {}).get("dpi", 300),
                   "page_types": [r.doc_type for r in records]}
            images = [r.working_path for r in records]
            texts = [r.ocr_text for r in records]
            out_path = self.export_fn(images, export_cfg, ctx, texts)
            self.db.update_document(doc_id, storage_path=str(out_path))
            if self.audit:
                self.audit.log("export", "system", document_id=doc_id,
                               path=str(out_path))
            index_path = self._write_index_file(
                doc_id, out_path, rt, export_cfg.get("index") or {},
                page_count=len(records), fields=document["extracted_fields"],
                qc_score=document.get("qc_score"))

            # multi-channel: project-enabled channels + rule-triggered ones
            targets = list(rt.channels)
            names = {c.name for c in targets}
            for trigger in document.get("trigger_channels", []):
                extra = rt.trigger_pool.get(trigger)
                if extra is not None and extra.name not in names:
                    targets.append(extra)
                    names.add(extra.name)

            delivered_status = None
            details = {}
            for channel in targets:
                result = self._deliver_with_retry(channel, str(out_path),
                                                  document)
                details[channel.name] = {"success": result.success,
                                         "checksum": result.checksum,
                                         **result.detail}
                if index_path is not None and result.success:
                    sent = self._deliver_with_retry(channel, str(index_path),
                                                    document)
                    details[channel.name]["index"] = {
                        "success": sent.success, "checksum": sent.checksum}
                    if not sent.success and self.audit:
                        self.audit.log("index_not_delivered", "system",
                                       document_id=doc_id, channel=channel.name)
                if result.success:
                    delivered_status = delivered_status or channel.status_value
                    if self.audit:
                        self.audit.log("deliver", "system",
                                       document_id=doc_id,
                                       channel=channel.name,
                                       destination=result.destination)
            self.db.update_document(
                doc_id,
                delivery_status=delivered_status or "pending",
                delivery_detail=details)
            self._emit("delivery", "done", document_id=doc_id,
                       delivery_status=delivered_status or "pending")
            self._maybe_complete_batch(document["batch_id"])
        self._pump(self.delivery_q, "delivery", handle)

    def _write_index_file(self, doc_id: str, pdf_path, rt: ProjectRuntime,
                          index_cfg: dict, page_count: int, fields: dict,
                          qc_score) -> Path | None:
        """Write the document's index file next to its PDF when the project
        asks for one. A failure never fails the document: it is audited (type
        only, values can be personal data) and the PDF is still delivered."""
        if not index_cfg.get("enabled"):
            return None
        try:
            row = self.db.get_document(doc_id)
            batch = self.db.get_batch(row["batch_id"]) if row is not None else None
            record = build_record(
                document_id=doc_id, project_name=rt.profile.get("name", ""),
                batch_id=row["batch_id"] if row is not None else "",
                operator=(batch["operator_user"] if batch is not None else "") or "",
                scanned_at=(row["created_at"] if row is not None else "") or "",
                qc_score=qc_score, page_count=page_count,
                pdf_path=pdf_path, fields=fields)
            path = write_index(pdf_path, record, index_cfg.get("format", "csv"))
            if index_cfg.get("running_csv"):
                append_running_csv(Path(pdf_path).parent, record)
            return path
        except Exception as e:
            log.warning("index file not written for %s: %s", doc_id,
                        type(e).__name__)
            if self.audit:
                self.audit.log("index_failed", "system", document_id=doc_id,
                               error=type(e).__name__)
            return None

    def _check_duplicate(self, document: dict, records: list,
                         rt: ProjectRuntime) -> dict | None:
        """Fingerprint the first non-blank page and compare with earlier
        documents (features.duplicate_check, HLD §4 Layer 5). A match
        sets duplicate_of + holds the document for review."""
        if not rt.profile.get("features", {}).get("duplicate_check"):
            return None
        first = next((r for r in records if not r.is_empty
                      and r.working_path), None)
        if first is None:
            return None
        try:
            from ocr.duplicate import find_duplicate, fingerprint
            md5, phash = fingerprint(first.working_path)
        except Exception as e:
            log.warning("duplicate fingerprint failed: %s", e)
            return None
        known = []
        for row in self.db.list_documents():
            try:
                fp = json.loads(row["qc_detail"] or "{}").get("fingerprint")
            except json.JSONDecodeError:
                continue
            if fp:
                known.append({"document_id": row["document_id"],
                              "md5": fp.get("md5"), "phash": fp.get("phash"),
                              "barcode": fp.get("barcode")})
        dup_id = find_duplicate(md5, phash, known,
                                barcode=document.get("barcode"))
        if dup_id:
            document["duplicate_of"] = dup_id
            document["validation_status"] = "pending_qc"
            document["flags"] = sorted(set(document["flags"]) | {"duplicate"})
            log.info("document %s flagged as duplicate of %s",
                     document["document_id"], dup_id)
        return {"md5": md5, "phash": phash,
                "barcode": document.get("barcode")}

    def _resubmit_inputs(self, document_id: str):
        """(row, page records, reason). reason is "" when the document can be
        re-exported, else a short operator-readable refusal (value-free)."""
        row = self.db.get_document(document_id)
        if row is None:
            return None, [], "unknown document"
        try:
            self._resolve(row["project_id"])
        except ProjectUnavailable as e:
            return row, [], f"project not available: {e}"
        detail = json.loads(row["qc_detail"] or "{}")
        pages = detail.get("pages", {})
        records = []
        for page_no in sorted(pages, key=int):
            info = pages[page_no]
            working = info.get("working_path")
            if not working or not Path(working).exists():
                continue
            job = ScanJob(batch_id=row["batch_id"],
                          project_id=row["project_id"],
                          page_no=int(page_no),
                          image_path=info.get("original_path", working))
            records.append(PageRecord(job=job, working_path=working,
                                      ocr_text=info.get("ocr_text", ""),
                                      doc_type=info.get("doc_type")))
        if not records:
            return row, [], REASON_NO_PAGE_IMAGES
        return row, records, ""

    def can_resubmit(self, document_id: str) -> tuple[bool, str]:
        """Whether resubmit_document would accept this document, and if not
        why. No side effects."""
        _row, _records, reason = self._resubmit_inputs(document_id)
        return (not reason, reason)

    def resubmit_document(self, document_id: str) -> bool:
        """Push an operator-approved review document into the delivery
        stage (QC review 'Accept → export'). Page images come from the
        paths recorded in qc_detail during QC. Returns False when the
        document is unknown or predates path recording."""
        row, records, reason = self._resubmit_inputs(document_id)
        if reason:
            log.warning("cannot re-export %s: %s", document_id, reason)
            return False
        fields = json.loads(row["extracted_fields"] or "{}")
        document = {
            "document_id": document_id,
            "batch_id": row["batch_id"],
            "project_id": row["project_id"],
            "barcode": fields.get("barcode"),
            "qc_score": row["qc_score"],
            "validation_status": "auto_approved",  # operator approved
            "extracted_fields": fields,
        }
        self.delivery_q.put({"document": document, "records": records})
        return True

    def _deliver_with_retry(self, channel, doc_path: str, metadata: dict):
        """Retry hard failures up to delivery_attempts; a queued result
        (e.g. CD/DVD staged awaiting burn) is not an error and is
        returned as-is."""
        result = None
        for attempt in range(1, self.delivery_attempts + 1):
            result = channel.deliver(doc_path, metadata)
            if result.success or result.detail.get("queued"):
                return result
            log.warning("delivery via %s failed (attempt %d/%d): %s",
                        channel.name, attempt, self.delivery_attempts,
                        result.detail.get("error"))
        return result

    def _maybe_complete_batch(self, batch_id: str) -> None:
        pending = [d for d in self.db.list_documents(batch_id)
                   if d["validation_status"] == "pending_qc"]
        if not pending:
            self.db.update_batch_status(batch_id, "complete")
            self._emit("batch", "complete", batch_id=batch_id)
