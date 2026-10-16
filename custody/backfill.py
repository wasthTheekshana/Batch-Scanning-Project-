"""Link documents scanned before bundles tracked their documents.

A document's pages are still on disk (the original scans recorded in its QC
detail), so each is read again for a custody QR label and the document is
linked to the bundle it names. Documents whose images are gone are counted,
never guessed at.
"""
from __future__ import annotations

import json
import logging
import os

from custody.bundle_manager import link_document, refresh_stage

log = logging.getLogger(__name__)


def link_earlier_documents(db, operator: str | None = None, audit=None,
                           decode=None) -> dict:
    """Returns {"documents": looked at, "linked": linked to a bundle,
    "no_label": no custody label found, "no_images": page images missing}."""
    if decode is None:
        import cv2

        from separation.barcode import decode_barcodes

        def decode(path):
            image = cv2.imread(path, cv2.IMREAD_UNCHANGED)
            return [] if image is None else decode_barcodes(image)
    result = {"documents": 0, "linked": 0, "no_label": 0, "no_images": 0}
    touched: set[str] = set()
    for doc in db.documents_without_bundle():
        result["documents"] += 1
        try:
            pages = json.loads(doc["qc_detail"] or "{}").get("pages", {})
        except (TypeError, ValueError):
            pages = {}
        paths = [info.get("original_path") for info in pages.values()
                 if isinstance(info, dict) and info.get("original_path")]
        paths = [p for p in paths if os.path.exists(p)]
        if not paths:
            result["no_images"] += 1
            continue
        bundles = set()
        for path in paths:
            try:
                for code in decode(path):
                    bundle = db.get_bundle_by_qr(str(code["value"]))
                    if bundle is not None:
                        bundles.add(bundle["bundle_id"])
            except Exception:
                log.exception("could not read a page of document %s", doc["document_id"])
        if not bundles:
            result["no_label"] += 1
            continue
        for bundle_id in bundles:
            link_document(db, bundle_id, doc["document_id"], doc["batch_id"])
            touched.add(bundle_id)
        result["linked"] += 1
    for bundle_id in touched:
        refresh_stage(db, bundle_id, operator, audit=audit)
    return result
