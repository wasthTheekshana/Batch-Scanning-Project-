"""Unified audit timeline: one chronological view over custody checkpoints
(custody_events) and processing actions (audit_log).

Read-only. The two tables are joined through existing columns only:
custody_events.batch_id links a bundle to its scan batches, batches own
documents, and audit_log rows hang off either a document_id or (for
batch-level actions such as "scan") a batch_id inside the detail JSON.
Custody checkpoints are also mirrored into audit_log by AuditLogger; those
mirrors are dropped so each checkpoint appears once, from custody_events.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

FIELDS = ["timestamp", "source", "action", "user", "bundle_id", "batch_id",
          "document_id", "detail"]

# audit_log mirror of a custody_events row; custody_events is the source
_MIRRORED_ACTION = "custody_checkpoint"


def _entry(timestamp, source, action, user, bundle_id, batch_id,
           document_id, detail, seq) -> dict:
    return {"timestamp": timestamp, "source": source, "action": action,
            "user": user, "bundle_id": bundle_id, "batch_id": batch_id,
            "document_id": document_id, "detail": detail or "{}",
            "_seq": seq}


def _custody_entries(db, where: str, args: tuple) -> list[dict]:
    with db._lock:
        rows = db.conn.execute(
            "SELECT rowid AS seq, * FROM custody_events WHERE " + where,
            args).fetchall()
    return [_entry(r["timestamp"], "custody", r["stage"], r["operator"],
                   r["bundle_id"], r["batch_id"], None, r["detail"],
                   r["seq"]) for r in rows]


def _audit_entries(db, batch_ids: list[str], document_ids: list[str],
                   bundle_by_batch: dict[str, str | None],
                   batch_by_doc: dict[str, str]) -> list[dict]:
    """audit_log rows for the given documents plus batch-level rows whose
    detail JSON names one of the batches."""
    clauses, args = [], []
    if document_ids:
        clauses.append("document_id IN (%s)" % ",".join("?" * len(document_ids)))
        args += document_ids
    if batch_ids:
        clauses.append("json_extract(detail, '$.batch_id') IN (%s)"
                       % ",".join("?" * len(batch_ids)))
        args += batch_ids
    if not clauses:
        return []
    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM audit_log WHERE action != ? AND (%s)"
            % " OR ".join(clauses), [_MIRRORED_ACTION] + args).fetchall()
    out = []
    for r in rows:
        try:
            batch_id = json.loads(r["detail"] or "{}").get("batch_id")
        except (TypeError, ValueError):
            batch_id = None
        batch_id = batch_id or batch_by_doc.get(r["document_id"])
        out.append(_entry(r["timestamp"], "audit", r["action"], r["user"],
                          bundle_by_batch.get(batch_id), batch_id,
                          r["document_id"], r["detail"], r["log_id"]))
    return out


def _documents_by_batch(db, batch_ids: list[str]) -> dict[str, str]:
    if not batch_ids:
        return {}
    with db._lock:
        rows = db.conn.execute(
            "SELECT document_id, batch_id FROM documents WHERE batch_id IN "
            "(%s)" % ",".join("?" * len(batch_ids)), batch_ids).fetchall()
    return {r["document_id"]: r["batch_id"] for r in rows}


def _assemble(db, custody: list[dict], batch_ids: list[str],
              bundle_by_batch: dict[str, str | None]) -> list[dict]:
    batch_by_doc = _documents_by_batch(db, batch_ids)
    audit = _audit_entries(db, batch_ids, list(batch_by_doc),
                           bundle_by_batch, batch_by_doc)
    entries = custody + audit
    entries.sort(key=lambda e: (e["timestamp"], e["source"] != "custody",
                                e["_seq"]))
    for e in entries:
        del e["_seq"]
    return entries


def bundle_timeline(db, bundle_id: str) -> list[dict]:
    """Custody checkpoints for the bundle plus everything that happened to
    the batches (and their documents) scanned from it."""
    custody = _custody_entries(db, "bundle_id=?", (bundle_id,))
    batch_ids = sorted({e["batch_id"] for e in custody if e["batch_id"]})
    return _assemble(db, custody, batch_ids,
                     {b: bundle_id for b in batch_ids})


def batch_timeline(db, batch_id: str) -> list[dict]:
    """Processing actions for the batch and its documents, plus the custody
    checkpoints recorded against it."""
    custody = _custody_entries(db, "batch_id=?", (batch_id,))
    bundles = {e["bundle_id"] for e in custody}
    bundle = next(iter(bundles)) if len(bundles) == 1 else None
    return _assemble(db, custody, [batch_id], {batch_id: bundle})


def document_timeline(db, document_id: str) -> list[dict]:
    """Audit rows for one document, tagged with its batch."""
    doc = db.get_document(document_id)
    if doc is None:
        return []
    batch_id = doc["batch_id"]
    entries = _audit_entries(db, [], [document_id], {}, {document_id: batch_id})
    entries.sort(key=lambda e: (e["timestamp"], e["_seq"]))
    for e in entries:
        del e["_seq"]
    return entries


def write_timeline_csv(entries: list[dict], out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for e in entries:
            w.writerow({k: ("" if e.get(k) is None else e[k]) for k in FIELDS})
    return out_path


def write_timeline_pdf(entries: list[dict], out_path: str | Path,
                       title: str = "Audit timeline") -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    cell = styles["BodyText"]
    cell.fontSize = 7
    cell.leading = 8.5
    story = [Paragraph(f"DOK Scanner — Audit Timeline: {title}",
                       styles["Title"]),
             Paragraph(f"{len(entries)} events", styles["Normal"]),
             Spacer(1, 4 * mm)]
    rows = [["Time (UTC)", "Source", "Action", "User", "Bundle", "Batch",
             "Document", "Detail"]]
    for e in entries:
        rows.append([e["timestamp"][:19].replace("T", " "), e["source"],
                     e["action"], e["user"] or "", (e["bundle_id"] or "")[:8],
                     (e["batch_id"] or "")[:8], (e["document_id"] or "")[:8],
                     Paragraph(_esc(e["detail"])[:200], cell)])
    t = Table(rows, hAlign="LEFT", repeatRows=1,
              colWidths=[34 * mm, 18 * mm, 30 * mm, 24 * mm, 18 * mm,
                         18 * mm, 20 * mm, None])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f5f6fa")])]))
    story.append(t)
    SimpleDocTemplate(str(out_path), pagesize=landscape(A4),
                      leftMargin=12 * mm, rightMargin=12 * mm,
                      topMargin=12 * mm, bottomMargin=12 * mm).build(story)
    return out_path


def export_timeline(entries: list[dict], out_path: str | Path,
                    title: str = "Audit timeline") -> Path:
    """Write entries as CSV or PDF, chosen by the file suffix."""
    suffix = Path(out_path).suffix.lower()
    if suffix == ".csv":
        return write_timeline_csv(entries, out_path)
    if suffix == ".pdf":
        return write_timeline_pdf(entries, out_path, title=title)
    raise ValueError(f"unsupported timeline format {suffix!r} (use .csv or .pdf)")


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))
