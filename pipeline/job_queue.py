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

import logging
import queue
import threading
import traceback
from pathlib import Path

import cv2

from shared.models import PageRecord, ScanJob, new_id
from pipeline.pipeline_custody import CUSTODY_SHEET_MAX_CHARS, CustodyMixin  # noqa: F401
from pipeline.delivery_stage import REASON_NO_PAGE_IMAGES, DeliveryMixin  # noqa: F401  (the screens import it from here)
from pipeline.indexing import IndexingMixin
from pipeline.pipeline_reissue import ReissueMixin
from pipeline.project_runtime import ProjectRuntime, ProjectUnavailable
from imaging.blank_detect import ink_density, is_blank
from imaging.crop import auto_crop
from imaging.deskew import deskew
from imaging.enhance import apply_profile, to_color_mode
from qc.pipeline import run_qc
from ocr.doc_types import enhancement_for, inherit_types, match_page
from ocr.orientation import turn, upright
from separation.barcode import decode_barcodes, filter_barcodes
from separation.rule_engine import RuleEngine, RuleSetError
from separation.splitter import split_documents

log = logging.getLogger(__name__)


def _starts_document(text: str, ocr_cfg: dict) -> bool:
    """True when the page is the title page of a document type that begins a
    new document (its `regex` matches and the type is marked starts_document)."""
    doc_type, title_page = match_page(text, ocr_cfg)
    return bool(doc_type and title_page and doc_type.get("starts_document"))


_SENTINEL = object()


class Pipeline(ReissueMixin, CustodyMixin, IndexingMixin, DeliveryMixin):
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
                    self._note_custody_codes(record, decoded)
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
            skip_ocr = (not record.custody_codes     # its text tells a label sheet from a document
                        and ((record.is_blank
                              and "blank_page" in separation.get("strategy", []))
                             or (record.is_empty
                                 and bool(separation.get("drop_blank_pages")))))
            if self.ocr_fn is not None and not skip_ocr:
                image = cv2.imread(record.job.image_path, cv2.IMREAD_UNCHANGED)
                result = self.ocr_fn(image)
                own = [b for b in record.barcodes       # the project's own barcodes
                       if str(b["value"]) not in record.custody_codes]
                if not own:        # a barcode page is known: leave it
                    # a page fed upside down or sideways reads as gibberish:
                    # read it the other ways up and keep the one that reads
                    record.rotation, image, result = upright(
                        image, result, self.ocr_fn)
                if result is not None:
                    record.ocr_text = result.text
                    record.ocr_confidence = result.confidence
                    record.ocr_words = list(getattr(result, "words", None) or [])
                self._extract_zone_fields(record, image, rt)
            self._decide_custody_sheet(
                record, has_text=self.ocr_fn is not None and not skip_ocr
                and bool(record.ocr_text))
            self._emit("ocr", "done", job_id=record.job.job_id,
                       batch_id=record.job.batch_id)
            self.enhance_q.put(record)
        self._pump(self.ocr_q, "ocr", handle)


    def _enhance_worker(self) -> None:
        def handle(record: PageRecord):
            rt = self._rt(record.job.batch_id, record.job.project_id)
            # a document type may ask for gentler (or stronger) treatment than the project
            profile = enhancement_for(rt.profile.get("ocr", {}),
                                      rt.profile.get("enhancement_profile", {}), record.doc_type)
            image = turn(cv2.imread(record.job.image_path, cv2.IMREAD_UNCHANGED),
                         record.rotation)
            if not record.is_empty:
                scanner = self._rt(record.job.batch_id,
                                   record.job.project_id).profile.get("scanner", {})
                dpi = int(scanner.get("dpi") or 300)
                if profile.get("deskew"):
                    image, _ = deskew(image)
                if profile.get("crop"):
                    image = auto_crop(image)
                image = apply_profile(image, profile, dpi)
                if scanner.get("color") == "bw":
                    # black and white is decided here, locally adaptive, from the
                    # grayscale scan (the scanner's own 1-bit mode loses thin strokes)
                    image = to_color_mode(image, "bw", dpi)
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
                    {"dpi": rt.profile.get("scanner", {}).get("dpi", 300),
                     "enhanced": True})      # the enhance step already filtered this page
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

        ocr_cfg = self._rt(batch_id, record.job.project_id).profile.get("ocr", {})
        page_dicts = [{"page_no": r.job.page_no, "image": None,
                       "starts_document": _starts_document(r.ocr_text, ocr_cfg),
                       "is_blank": r.is_blank, "ink_density": r.ink_density,
                       "barcodes": r.barcodes,
                       "is_custody_label": r.is_custody_label,
                       "ocr_text": r.ocr_text,
                       "record": r} for r in pages]
        rt = self._rt(batch_id, record.job.project_id)
        docs = split_documents(page_dicts, rt.profile.get("separation", {}))
        self._audit_excluded_pages(batch_id, page_dicts, docs)
        self._emit("separation", "done", batch_id=batch_id,
                   documents=len(docs))
        if not docs:
            # every page was left out (label sheets, separators, blanks): there
            # is nothing to review, so the batch must not sit at "qc_pending"
            # and the operator must be told why
            self.db.update_batch_status(batch_id, "complete", len(pages))
            self._emit("batch", "no_documents", batch_id=batch_id,
                       pages=len(pages),
                       reasons=self._excluded_reasons(page_dicts, docs))
            return
        self.db.update_batch_status(batch_id, "qc_pending", len(pages))
        for doc in docs:
            self.rule_q.put({"batch_id": batch_id, "doc": doc})

    def _audit_excluded_pages(self, batch_id: str, page_dicts: list,
                              docs: list) -> None:
        """Pages that ended up in no document (cover sheets, custody labels,
        discarded blank pages) are recorded, so a page is never dropped
        without a trace."""
        reason = Pipeline._excluded_reasons(page_dicts, docs)
        if not reason or self.audit is None:
            return
        self.audit.log("pages_excluded", "system", batch_id=batch_id,
                       count=len(reason), reasons=reason)

    @staticmethod
    def _excluded_reasons(page_dicts: list, docs: list) -> dict:
        """{page_no: why} for the pages that ended up in no document."""
        kept = {p["page_no"] for d in docs for p in d["pages"]}
        return {p["page_no"]: ("custody_label" if p["is_custody_label"]
                               else "separator" if p["barcodes"]
                               else "blank")
                for p in page_dicts if p["page_no"] not in kept}

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
            if low_conf:        # the Index tab highlights these for the operator
                qc_detail["low_conf_fields"] = sorted(low_conf)
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
            self._link_custody(document["document_id"], item["batch_id"], records)
            if self.audit:
                self.audit.log("rule_engine", "system",
                               document_id=document["document_id"],
                               status=document["validation_status"])
            self._emit("rule", "done", document_id=document["document_id"],
                       validation_status=document["validation_status"])
            self.delivery_q.put({"document": document, "records": records})
        self._pump(self.rule_q, "rule", handle)


