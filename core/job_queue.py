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

from core.models import PageRecord, ScanJob, new_id
from imaging.blank_detect import is_blank
from imaging.crop import auto_crop
from imaging.deskew import deskew
from imaging.enhance import apply_profile
from qc.pipeline import run_qc
from separation.barcode import decode_barcodes
from separation.rule_engine import RuleEngine, RuleSetError
from separation.splitter import split_documents

log = logging.getLogger(__name__)

_SENTINEL = object()


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
                 delivery_attempts: int = 3):
        self.db = db
        self.profile = profile
        self.channels = channels
        # channels addressable by the trigger_delivery rule action, even
        # when not project-enabled (Phase 3 multi-channel delivery)
        self.trigger_pool = trigger_pool or {c.name: c for c in channels}
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
        if self.db.get_project(self.profile["project_id"]) is None:
            self.db.insert_project(self.profile["project_id"],
                                   self.profile["name"], self.profile)
        with self._lock:
            self._batches[batch_id] = {"expected": len(jobs), "pages": [],
                                       "counter": 0, "operator": operator}
        self.db.insert_batch(batch_id, self.profile["project_id"], operator)
        for job in jobs:
            self.db.upsert_job(job.job_id, job.batch_id, job.project_id,
                               job.page_no, "queued", job.image_path)
            self.ingest_q.put(job)

    def recover(self) -> list[ScanJob]:
        """Re-queue unfinished jobs from a previous run (HLD §14.2).
        Recovered jobs are grouped per batch and resubmitted."""
        rows = self.db.get_incomplete_jobs()
        by_batch: dict[str, list[ScanJob]] = {}
        for r in rows:
            job = ScanJob(batch_id=r["batch_id"], project_id=r["project_id"],
                          page_no=r["page_no"], image_path=r["payload_path"],
                          job_id=r["job_id"])
            by_batch.setdefault(job.batch_id, []).append(job)
        recovered = []
        for batch_id, jobs in by_batch.items():
            with self._lock:
                self._batches[batch_id] = {"expected": len(jobs), "pages": [],
                                           "counter": 0, "operator": "recovery"}
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
            sep_cfg = self.profile.get("separation", {})
            record.is_blank = is_blank(
                image, sep_cfg.get("blank_threshold", 0.02))
            if "barcode" in sep_cfg.get("strategy", []):
                record.barcodes = decode_barcodes(image)
            self._emit("ingest", "done", job_id=job.job_id,
                       batch_id=job.batch_id, page_no=job.page_no)
            self.ocr_q.put(record)
        self._pump(self.ingest_q, "ingest", handle)

    def _ocr_worker(self) -> None:
        def handle(record: PageRecord):
            if self.ocr_fn is not None and not record.is_blank:
                image = cv2.imread(record.job.image_path, cv2.IMREAD_UNCHANGED)
                result = self.ocr_fn(image)
                if result is not None:
                    record.ocr_text = result.text
                    record.ocr_confidence = result.confidence
            self._emit("ocr", "done", job_id=record.job.job_id,
                       batch_id=record.job.batch_id)
            self.enhance_q.put(record)
        self._pump(self.ocr_q, "ocr", handle)

    def _enhance_worker(self) -> None:
        def handle(record: PageRecord):
            profile = self.profile.get("enhancement_profile", {})
            image = cv2.imread(record.job.image_path, cv2.IMREAD_UNCHANGED)
            if not record.is_blank:
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
            qc_cfg = self.profile.get("qc", {"enabled": False})
            if record.is_blank:
                record.qc_score, record.qc_grade = 100, "PASS"
            else:
                image = cv2.imread(record.working_path, cv2.IMREAD_UNCHANGED)
                fixed, verdict = run_qc(
                    image, qc_cfg,
                    {"dpi": self.profile.get("scanner", {}).get("dpi", 300)})
                cv2.imwrite(record.working_path, fixed)
                record.qc_score = verdict.score
                record.qc_grade = verdict.grade
                record.qc_detail = {**verdict.detail, "flags": verdict.flags}
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
                       "is_blank": r.is_blank, "barcodes": r.barcodes,
                       "record": r} for r in pages]
        docs = split_documents(page_dicts, self.profile.get("separation", {}))
        self.db.update_batch_status(batch_id, "qc_pending", len(pages))
        self._emit("separation", "done", batch_id=batch_id,
                   documents=len(docs))
        for doc in docs:
            self.rule_q.put({"batch_id": batch_id, "doc": doc})

    def _rule_worker(self) -> None:
        def handle(item: dict):
            doc = item["doc"]
            records = [p["record"] for p in doc["pages"]]
            worst = min((r.qc_score for r in records), default=100)
            grades = {r.qc_grade for r in records}
            document = {
                "document_id": new_id(),
                "batch_id": item["batch_id"],
                "barcode": doc.get("barcode"),
                "qc_score": worst,
                "validation_status": "auto_approved"
                if grades <= {"PASS"} else "pending_qc",
                "extracted_fields": {},
                "ocr_text": " ".join(r.ocr_text for r in records if r.ocr_text),
                "flags": sorted({f for r in records
                                 for f in r.qc_detail.get("flags", [])}),
            }
            if doc.get("barcode"):
                document["extracted_fields"]["barcode"] = doc["barcode"]

            rules = self.profile.get("rules") or {}
            if rules.get("rules"):
                try:
                    RuleEngine(rules).evaluate(document)
                except RuleSetError as e:
                    log.error("rule set invalid, skipping rules: %s", e)

            page_nos = [p["page_no"] for p in doc["pages"]]
            self.db.insert_document(
                document["document_id"], item["batch_id"],
                self.profile["project_id"],
                page_range=f"{min(page_nos)}-{max(page_nos)}",
                enhancement_profile="default",
                extracted_fields=document["extracted_fields"],
                validation_status=document["validation_status"],
                qc_score=worst,
                qc_detail={"pages": {str(r.job.page_no): r.qc_detail
                                     for r in records}})
            if self.audit:
                self.audit.log("rule_engine", "system",
                               document_id=document["document_id"],
                               status=document["validation_status"])
            self._emit("rule", "done", document_id=document["document_id"],
                       validation_status=document["validation_status"])
            self.delivery_q.put({"document": document, "records": records})
        self._pump(self.rule_q, "rule", handle)

    def _delivery_worker(self) -> None:
        def handle(item: dict):
            document, records = item["document"], item["records"]
            doc_id = document["document_id"]

            if document["validation_status"] != "auto_approved":
                # WARN/FAIL documents wait in the review queue; the QC
                # review UI re-submits them for export after approval.
                self._emit("delivery", "held_for_review", document_id=doc_id)
                return

            export_cfg = self.profile.get("export", {})
            with self._lock:
                state = self._batches[document["batch_id"]]
                state["counter"] += 1
                counter = state["counter"]
            ctx = {"batch_id": document["batch_id"],
                   "counter": counter,
                   "barcode": document.get("barcode") or "",
                   "date": __import__("datetime").datetime.now().strftime("%Y%m%d"),
                   "fields": document["extracted_fields"],
                   "output_folder": document.get("output_folder", ""),
                   "dpi": self.profile.get("scanner", {}).get("dpi", 300)}
            images = [r.working_path for r in records]
            texts = [r.ocr_text for r in records]
            out_path = self.export_fn(images, export_cfg, ctx, texts)
            self.db.update_document(doc_id, storage_path=str(out_path))
            if self.audit:
                self.audit.log("export", "system", document_id=doc_id,
                               path=str(out_path))

            # multi-channel: project-enabled channels + rule-triggered ones
            targets = list(self.channels)
            names = {c.name for c in targets}
            for trigger in document.get("trigger_channels", []):
                extra = self.trigger_pool.get(trigger)
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
