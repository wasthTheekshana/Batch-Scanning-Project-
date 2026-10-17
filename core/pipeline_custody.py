"""How the processing stages deal with custody-bundle label sheets.

A page carrying a custody QR is either a label sheet (left out of the documents) or a document
that merely carries the label; see _decide_custody_sheet. Methods of Pipeline (core/job_queue.py)."""
from __future__ import annotations

import logging
import re

from core.models import PageRecord

log = logging.getLogger(__name__)

# a page with a custody label and less text than this is a label sheet, not a document
CUSTODY_SHEET_MAX_CHARS = 200


class CustodyMixin:
    def _note_custody_codes(self, record: PageRecord, decoded: list) -> None:
        """Custody QR labels among the barcodes of a page. A known bundle gets
        its "scanning" checkpoint (once per batch, however many pages carry the
        label). The label's value stays among the page's barcodes: on a document
        page it separates documents like any other barcode the project uses.
        Whether the PAGE is a label sheet (left out of the documents) or a document that
        merely carries the label is decided after OCR, from how much else is
        written on it (see _decide_custody_sheet)."""
        from custody.bundle_manager import QR_PREFIX, record_checkpoint
        job = record.job
        with self._lock:
            state = self._batches.get(job.batch_id, {})
            operator = state.get("operator", "system")
            seen = state.setdefault("custody_seen", set())
        for bc in decoded:
            value = str(bc["value"])
            try:
                bundle = self.db.get_bundle_by_qr(value)
                if bundle is not None:
                    record.custody_codes.append(value)
                    if bundle["bundle_id"] not in seen:
                        seen.add(bundle["bundle_id"])
                        record_checkpoint(self.db, bundle["bundle_id"], "scanning",
                                          operator, batch_id=job.batch_id,
                                          audit=self.audit)
                elif value.startswith(QR_PREFIX):
                    record.custody_codes.append(value)
                    record.custody_unknown = True
            except Exception:
                log.exception("custody checkpoint failed for barcode %s on job %s",
                              value, job.job_id)

    def _link_custody(self, document_id: str, batch_id: str, records: list) -> None:
        """Tie the document to every custody bundle whose QR label is on one of
        its pages, then move those bundles on if all their documents have had
        their QC decision. Never lets a custody problem fail the document."""
        try:
            from custody.bundle_manager import link_document, refresh_stage
            with self._lock:
                operator = self._batches.get(batch_id, {}).get("operator", "system")
            codes = {c for r in records for c in r.custody_codes}
            for code in sorted(codes):
                bundle = self.db.get_bundle_by_qr(code)
                if bundle is None:
                    continue
                link_document(self.db, bundle["bundle_id"], document_id, batch_id)
                refresh_stage(self.db, bundle["bundle_id"], operator,
                              audit=self.audit)
        except Exception:
            log.exception("custody link failed for document %s", document_id)

    def _decide_custody_sheet(self, record: PageRecord, has_text: bool) -> None:
        """A page with a custody QR is a label SHEET (never document content)
        only when hardly anything else is written on it. A document that
        carries the bundle's label (the same sticker on every invoice) stays
        in the batch. With no text to judge by, the page is treated as a
        sheet, as before."""
        if not record.custody_codes:
            return
        if has_text:
            text = record.ocr_text
            for code in record.custody_codes:
                text = text.replace(code, " ")
            record.is_custody_label = (
                len(re.sub(r"\W", "", text)) < CUSTODY_SHEET_MAX_CHARS)
        else:
            record.is_custody_label = True
        if record.custody_unknown:
            operator = self._batches.get(record.job.batch_id, {}).get(
                "operator", "system")
            if self.audit is not None:
                self.audit.log("custody_label_unknown", operator,
                               batch_id=record.job.batch_id,
                               page_no=record.job.page_no,
                               labels=len(record.custody_codes))
            self._emit("custody", "unknown_label", batch_id=record.job.batch_id,
                       page_no=record.job.page_no,
                       left_out=record.is_custody_label)
