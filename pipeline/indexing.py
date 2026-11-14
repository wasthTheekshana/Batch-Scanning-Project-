"""Reading index fields from a page, and the checks made on them (lookup values, duplicates).

A mixin of pipeline.job_queue.Pipeline: it relies on the pipeline's state and queues.
"""
from __future__ import annotations

import json
import logging

from ocr.doc_types import page_zone_groups
from pipeline.project_runtime import ProjectRuntime
from shared.models import PageRecord

log = logging.getLogger("pipeline.job_queue")      # the same logger as before the split
# A field's lookup table only starts rejecting unrecognised values once
# enough history exists to trust it — otherwise every project's first few
# documents would flag every field as unknown.
_LOOKUP_MIN_SAMPLES = 5


class IndexingMixin:
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
