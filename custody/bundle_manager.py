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

import uuid
from io import BytesIO
from pathlib import Path

import qrcode
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from core.models import new_id

VALID_STAGES = {"receipt", "prep", "scanning", "qc", "storage", "return",
                "damaged", "missing", "compromised"}


def create_bundle(db, project_id: str, label: str, audit=None) -> dict:
    """Register a new physical bundle and log its receipt checkpoint.
    The QR value is a fresh uuid, not the bundle_id itself, so a bundle
    can be re-issued a new label sheet without changing its identity."""
    bundle_id = new_id()
    qr_value = f"DOK-BUNDLE-{uuid.uuid4().hex[:12].upper()}"
    db.insert_bundle(bundle_id, project_id, label, qr_value)
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


def generate_label_sheet(bundle: dict, out_path: str | Path) -> Path:
    """A one-page PDF separator sheet: a QR code encoding the bundle's
    qr_value plus human-readable bundle_id/label text underneath. This
    sheet is printed and placed as the first page of the physical
    bundle when it goes to the scanner - scanning it is what lets
    record_checkpoint_from_barcode (Task 4) auto-log the "scanning"
    checkpoint without any extra operator action."""
    out_path = Path(out_path)
    qr_img = qrcode.make(bundle["qr_value"])
    buf = BytesIO()
    qr_img.save(buf, format="PNG")
    buf.seek(0)

    c = canvas.Canvas(str(out_path), pagesize=letter)
    page_w, _ = letter
    qr_size = 200
    x = (page_w - qr_size) / 2
    c.drawImage(ImageReader(buf), x, 500, width=qr_size, height=qr_size)
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(page_w / 2, 470, bundle["label"])
    c.setFont("Helvetica", 11)
    c.drawCentredString(page_w / 2, 450, f"Bundle ID: {bundle['bundle_id']}")
    c.drawCentredString(page_w / 2, 434, f"QR: {bundle['qr_value']}")
    c.save()
    return out_path


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
        mismatch = (counts["received"] > 0 and counts["scanned"] == 0) or \
                   (escalated and counts["returned"] == 0)
        rows.append({"bundle_id": bundle["bundle_id"],
                     "label": bundle["label"],
                     "status": bundle["status"],
                     "mismatch": mismatch, **counts})
    return rows
