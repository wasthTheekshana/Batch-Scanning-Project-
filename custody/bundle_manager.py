"""Chain-of-custody tracking (DOK_Scanner_Enhancement_Plan §3.2).

A bundle is a physical box/folder of paper handed over at intake. Its
lifecycle is a sequence of checkpoints - receipt, prep, scanning, qc,
storage, return - plus the escalation stages (damaged/missing) a bundle
can be flagged into at any point. Every checkpoint both updates the
bundle's current status and appends an immutable custody_events row, so
the reconciliation view (Task 5) can compare "received" against every
later stage.
"""
from __future__ import annotations

import math
import uuid
from io import BytesIO
from pathlib import Path

import qrcode
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from core.models import new_id

QR_PREFIX = "DOK-BUNDLE-"          # every custody label QR value starts with this

VALID_STAGES = {"receipt", "prep", "scanning", "qc", "storage", "return",
                "damaged", "missing", "compromised"}


def create_bundle(db, project_id: str, label: str, audit=None,
                  expected_documents: int | None = None) -> dict:
    """Register a new physical bundle and log its receipt checkpoint.
    The QR value is a fresh uuid, not the bundle_id itself, so a bundle
    can be re-issued a new label sheet without changing its identity."""
    bundle_id = new_id()
    qr_value = f"{QR_PREFIX}{uuid.uuid4().hex[:12].upper()}"
    db.insert_bundle(bundle_id, project_id, label, qr_value,
                     expected_documents=expected_documents or None)
    record_checkpoint(db, bundle_id, "receipt", operator=None, audit=audit)
    return dict(db.get_bundle(bundle_id))


def record_checkpoint(db, bundle_id: str, stage: str, operator: str | None,
                      batch_id: str | None = None, detail: dict | None = None,
                      audit=None) -> None:
    """Log one custody checkpoint and move the bundle's status to it."""
    if stage not in VALID_STAGES:
        raise ValueError(f"unknown stage: {stage!r} (must be one of "
                         f"{sorted(VALID_STAGES)})")
    db.insert_custody_event(new_id(), bundle_id, stage, operator,
                            batch_id=batch_id, detail=detail)
    db.update_bundle_status(bundle_id, stage)
    if audit:
        audit.log("custody_checkpoint", operator or "system",
                  bundle_id=bundle_id, stage=stage, batch_id=batch_id)


def record_checkpoint_from_barcode(db, barcode_value: str, stage: str,
                                   operator: str | None,
                                   batch_id: str | None = None,
                                   audit=None) -> bool:
    """Look up a decoded barcode value against known bundle QR values and
    log a checkpoint if it matches. Returns False (no-op) for any
    barcode that isn't a custody label - e.g. a document-separator
    barcode from separation/barcode.py's existing document-splitting
    use - so this never interferes with non-custody scanning."""
    bundle = db.get_bundle_by_qr(barcode_value)
    if bundle is None:
        return False
    record_checkpoint(db, bundle["bundle_id"], stage, operator,
                      batch_id=batch_id, audit=audit)
    return True


MAX_LABEL_COPIES = 24      # the most QR codes that stay readable on one page


def generate_label_sheet(bundle: dict, out_path: str | Path,
                         copies: int = 1) -> Path:
    """A one-page PDF separator sheet: `copies` identical QR codes encoding the
    bundle's qr_value, each with the bundle's label and ids underneath, laid
    out in a grid. The sheet is printed and placed in the physical bundle when
    it goes to the scanner - scanning any one of the codes is what lets the
    pipeline auto-log the "scanning" checkpoint. Several copies let the sheet
    be cut into stickers for the folders of one bundle."""
    if not 1 <= copies <= MAX_LABEL_COPIES:
        raise ValueError(f"copies must be 1 to {MAX_LABEL_COPIES}, got {copies}")
    out_path = Path(out_path)
    qr_img = qrcode.make(bundle["qr_value"])
    buf = BytesIO()
    qr_img.save(buf, format="PNG")
    buf.seek(0)
    qr_reader = ImageReader(buf)

    label_text = bundle["label"]
    if len(label_text) > 40:
        label_text = label_text[:37] + "..."

    c = canvas.Canvas(str(out_path), pagesize=letter)
    page_w, page_h = letter
    if copies == 1:
        qr_size = 200
        c.drawImage(qr_reader, (page_w - qr_size) / 2, 500,
                    width=qr_size, height=qr_size)
        c.setFont("Helvetica-Bold", 16)
        c.drawCentredString(page_w / 2, 470, label_text)
        c.setFont("Helvetica", 11)
        c.drawCentredString(page_w / 2, 450, f"Bundle ID: {bundle['bundle_id']}")
        c.drawCentredString(page_w / 2, 434, f"QR: {bundle['qr_value']}")
    else:
        margin = 36
        cols = math.ceil(math.sqrt(copies))
        rows = math.ceil(copies / cols)
        cell_w = (page_w - 2 * margin) / cols
        cell_h = (page_h - 2 * margin) / rows
        text_h = 34                                 # label + qr value under the code
        qr_size = min(cell_w - 12, cell_h - text_h - 8, 200)
        for i in range(copies):
            row, col = divmod(i, cols)
            cx = margin + (col + 0.5) * cell_w
            top = page_h - margin - row * cell_h
            c.drawImage(qr_reader, cx - qr_size / 2, top - 4 - qr_size,
                        width=qr_size, height=qr_size)
            c.setFont("Helvetica-Bold", 9)
            c.drawCentredString(cx, top - qr_size - 16, label_text[:int(cell_w / 5)])
            c.setFont("Helvetica", 6)
            c.drawCentredString(cx, top - qr_size - 26, bundle["qr_value"])
    c.save()
    return out_path


# the stages scanning moves a bundle through by itself; later stages (storage,
# return) and incidents are physical events an operator records, and a scan
# must never move a bundle backwards out of them
_AUTOMATIC_FROM = {"receipt", "received", "prep", "scanning", "qc"}
_DECIDED = {"auto_approved", "corrected", "rejected"}


def link_document(db, bundle_id: str, document_id: str, batch_id: str) -> None:
    """The document was scanned under this bundle's QR label."""
    db.link_bundle_document(bundle_id, document_id, batch_id)


def refresh_stage(db, bundle_id: str, operator: str | None, audit=None) -> list[str]:
    """Move a bundle on from "scanning" to "qc" for each scan (batch) in which
    every one of its documents has had its QC decision (approved, corrected or
    rejected). Logged once per bundle and batch. Returns the batches advanced."""
    bundle = db.get_bundle(bundle_id)
    if bundle is None or bundle["status"] not in _AUTOMATIC_FROM:
        return []
    by_batch: dict[str, list[str]] = {}
    for doc in db.list_bundle_documents(bundle_id):
        by_batch.setdefault(doc["batch_id"], []).append(doc["validation_status"])
    done = {e["batch_id"] for e in db.list_custody_events(bundle_id)
            if e["stage"] == "qc"}
    advanced = []
    for batch_id, statuses in by_batch.items():
        if batch_id in done or not all(s in _DECIDED for s in statuses):
            continue
        record_checkpoint(db, bundle_id, "qc", operator, batch_id=batch_id,
                          detail={"documents": len(statuses)}, audit=audit)
        advanced.append(batch_id)
    return advanced


def refresh_for_document(db, document_id: str, operator: str | None,
                         audit=None) -> None:
    """After a document's QC decision, move on every bundle it belongs to."""
    for bundle_id in db.bundles_of_document(document_id):
        refresh_stage(db, bundle_id, operator, audit=audit)


def bundle_summary(db, bundle_id: str) -> dict:
    """What a bundle holds right now: counts of its documents by QC decision
    and delivery."""
    docs = db.list_bundle_documents(bundle_id)
    bundle = db.get_bundle(bundle_id)
    expected = bundle["expected_documents"] if bundle is not None else None
    counts = {"documents": len(docs), "approved": 0, "corrected": 0,
              "rejected": 0, "waiting_qc": 0, "delivered": 0}
    for d in docs:
        status = d["validation_status"]
        if status == "auto_approved":
            counts["approved"] += 1
        elif status == "corrected":
            counts["corrected"] += 1
        elif status == "rejected":
            counts["rejected"] += 1
        else:
            counts["waiting_qc"] += 1
        if str(d["delivery_status"] or "").startswith("delivered"):
            counts["delivered"] += 1
    if expected:                      # the box should hold this many documents
        counts["expected"] = expected
        counts["missing"] = max(0, expected - len(docs))
        counts["extra"] = max(0, len(docs) - expected)
    return counts


_ESCALATION_STAGES = {"damaged", "missing", "compromised"}


def reconciliation_summary(db, project_id: str | None = None) -> list[dict]:
    """One row per bundle: how many times each key stage was logged,
    and a mismatch flag - True when the bundle was received but never
    scanned, or was ever escalated (damaged/missing/compromised) and
    hasn't since been returned. This is deliberately a simple per-bundle
    reconciliation (each stage should happen once per bundle in the
    normal flow); a bundle that legitimately went through a stage more
    than once (e.g. rescanned) will show a count > 1, which is still
    correct information for a reviewer even though this function does
    not itself decide whether that's expected."""
    rows = []
    for bundle in db.list_bundles(project_id):
        events = db.list_custody_events(bundle["bundle_id"])
        counts = {"received": 0, "scanned": 0, "qc": 0, "storage": 0,
                  "returned": 0}
        stage_to_count_key = {"receipt": "received", "scanning": "scanned",
                              "qc": "qc", "storage": "storage",
                              "return": "returned"}
        escalated = False
        for e in events:
            key = stage_to_count_key.get(e["stage"])
            if key:
                counts[key] += 1
            if e["stage"] in _ESCALATION_STAGES:
                escalated = True
        found = len(db.list_bundle_documents(bundle["bundle_id"]))
        expected = bundle["expected_documents"]
        # fewer or more documents than the box is expected to hold (once it has
        # been scanned at all: before that the "never scanned" test speaks)
        count_off = bool(expected) and counts["scanned"] > 0 and found != expected
        mismatch = ((counts["received"] > 0 and counts["scanned"] == 0)
                    or (escalated and counts["returned"] == 0) or count_off)
        rows.append({"bundle_id": bundle["bundle_id"],
                     "label": bundle["label"],
                     "status": bundle["status"],
                     "documents": found, "expected": expected,
                     "mismatch": mismatch, **counts})
    return rows


def flag_incident(db, bundle_id: str, kind: str, operator: str,
                  note: str = "", audit=None) -> None:
    """Escalate a bundle as damaged/missing/compromised. This is the
    minimal hook the full Incident & Insurance-Linked workflow
    (Enhancement Plan §3.6, a separate later plan) will build on - it
    reuses record_checkpoint so the escalation shows up in the same
    custody_events trail and reconciliation_summary (Task 5) picks it
    up automatically via _ESCALATION_STAGES."""
    record_checkpoint(db, bundle_id, kind, operator, detail={"note": note},
                      audit=audit)
