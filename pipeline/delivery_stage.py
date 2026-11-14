"""The last stage: the searchable PDF's text layer, export, delivery with retries, the index file, and re-submitting a document.

A mixin of pipeline.job_queue.Pipeline: it relies on the pipeline's state and queues.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import cv2

from export.index_export import append_running_csv, build_record, write_index
from pipeline.project_runtime import ProjectRuntime, ProjectUnavailable
from shared.models import PageRecord, ScanJob

log = logging.getLogger("pipeline.job_queue")      # the same logger as before the split
TEXT_LAYER_WORKERS = 4
# Pipeline.can_resubmit reason for a document whose page images are gone
# (the one refusal the QC review still lets an approval through).
REASON_NO_PAGE_IMAGES = "page images unavailable"


class DeliveryMixin:
    def _text_layer(self, images: list[str], texts: list[str]):
        """Words and text for a searchable PDF, read from the exported page
        images themselves. The OCR done at ingest saw the page before
        deskew, crop and QC changed it, so its word boxes would not line up
        with the final image. A page that cannot be read again keeps the text
        found at ingest and goes out without word boxes."""
        words: list[list] = []
        out_texts: list[str] = []

        def read(pair):
            path, early = pair
            page_words, text = [], early
            if self.ocr_fn is not None:
                try:
                    result = self.ocr_fn(cv2.imread(path, cv2.IMREAD_UNCHANGED))
                    if result is not None:
                        page_words = list(getattr(result, "words", None) or [])
                        text = result.text or early
                except Exception:
                    log.warning("could not read %s again for the PDF text "
                                "layer", Path(path).name)
            return page_words, text

        # each page is a separate Tesseract process: read the pages of a document side by side
        from concurrent.futures import ThreadPoolExecutor
        pairs = list(zip(images, texts))
        with ThreadPoolExecutor(max_workers=max(1, min(TEXT_LAYER_WORKERS, len(pairs)))) as pool:
            for page_words, text in pool.map(read, pairs):
                words.append(page_words)
                out_texts.append(text)
        return out_texts, words

    def _delivery_worker(self) -> None:
        def handle(item: dict):
            if "reissue_index" in item:
                self._do_reissue_index(item["reissue_index"])
                return
            if "reissue_types" in item:
                self._do_reissue_types(item["reissue_types"])
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
                   "document_id": doc_id,
                   "counter": counter,
                   "barcode": document.get("barcode") or "",
                   "date": __import__("datetime").datetime.now().strftime("%Y%m%d"),
                   "fields": document["extracted_fields"],
                   "output_folder": document.get("output_folder", ""),
                   "dpi": rt.profile.get("scanner", {}).get("dpi", 300),
                   "page_types": [r.doc_type for r in records]}
            images = [r.working_path for r in records]
            texts = [r.ocr_text for r in records]
            if export_cfg.get("format", "searchable_pdf") in (
                    "searchable_pdf", "pdf_a"):
                texts, ctx["ocr_words"] = self._text_layer(images, texts)
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
