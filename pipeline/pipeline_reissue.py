"""Re-delivery after an operator corrects a document: its index values or its page types.

The exported PDF's bookmarks and the index file are rewritten and sent again to the channels the
document already reached. Methods of Pipeline (pipeline/job_queue.py), kept here so that file stays
about the six processing stages. They run on the delivery thread."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pipeline.project_runtime import ProjectUnavailable

log = logging.getLogger(__name__)


class ReissueMixin:
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

    def reissue_page_types(self, document_id: str) -> bool:
        """After an operator corrected a document's page types: rewrite the
        exported PDF's bookmarks in place and deliver it (and its index file)
        again. True when queued; False when the document has no exported PDF
        or its project is unavailable. Runs on the delivery thread."""
        row = self.db.get_document(document_id)
        if row is None or not row["storage_path"]                 or not Path(row["storage_path"]).is_file():
            return False
        try:
            self._resolve(row["project_id"])
        except ProjectUnavailable:
            return False
        self.delivery_q.put({"reissue_types": document_id})
        return True

    def _do_reissue_types(self, document_id: str) -> None:
        row = self.db.get_document(document_id)
        if row is None:
            return
        path = Path(row["storage_path"] or "")
        try:
            rt = self._resolve(row["project_id"])
        except ProjectUnavailable:
            return
        pages = json.loads(row["qc_detail"] or "{}").get("pages", {})
        types = [(pages[n] or {}).get("doc_type") for n in sorted(pages, key=int)]
        sent_to = []
        if path.suffix.lower() == ".pdf" and path.is_file():
            from export.batch_exporter import set_type_bookmarks
            try:
                set_type_bookmarks(path, types)
            except Exception:
                log.warning("could not rewrite the bookmarks of %s", path.name)
                return
            details = json.loads(row["delivery_detail"] or "{}")
            for channel in rt.channels:
                if channel.name == "cd_dvd" or not details.get(channel.name, {}).get("success"):
                    continue         # a disc queue would stage a second copy
                sent = self._deliver_with_retry(
                    channel, str(path),
                    {"document_id": document_id, "project_id": row["project_id"]})
                details[channel.name]["success"] = sent.success
                details[channel.name]["checksum"] = sent.checksum
                sent_to.append(channel.name)
            self.db.update_document(document_id, delivery_detail=details)
        if self.audit:
            self.audit.log("page_types_reissued", "system", document_id=document_id,
                           channels=sent_to)
        # the index file carries the document types too
        if ((rt.profile.get("export") or {}).get("index") or {}).get("enabled"):
            self._do_reissue_index(document_id)

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
