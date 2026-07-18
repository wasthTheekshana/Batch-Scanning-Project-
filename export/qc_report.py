"""QC report PDF (HLD §10.3): batch statistics, per-document QC results,
and an optional per-document audit-trail section."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)


def batch_statistics(db, batch_id: str) -> dict:
    docs = [dict(d) for d in db.list_documents(batch_id)]
    scores = [d["qc_score"] for d in docs if d["qc_score"] is not None]
    by_status: dict[str, int] = {}
    for d in docs:
        by_status[d["validation_status"]] = \
            by_status.get(d["validation_status"], 0) + 1
    delivered = sum(1 for d in docs if d["delivery_status"] != "pending")
    duplicates = sum(1 for d in docs if d["duplicate_of"])
    return {"documents": len(docs), "by_status": by_status,
            "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
            "min_score": min(scores) if scores else None,
            "delivered": delivered, "duplicates": duplicates, "docs": docs}


def write_qc_report(db, batch_id: str, out_path: str | Path,
                    include_audit_trail: bool = True) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    story = []

    batch = db.conn.execute("SELECT * FROM batches WHERE batch_id=?",
                            (batch_id,)).fetchone()
    stats = batch_statistics(db, batch_id)

    story.append(Paragraph("DOK Scanner — QC Report", styles["Title"]))
    story.append(Paragraph(
        f"Batch {batch_id} · operator "
        f"{batch['operator_user'] if batch else 'n/a'} · generated "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M')}", styles["Normal"]))
    story.append(Spacer(1, 6 * mm))

    summary = [["Documents", "Avg score", "Min score", "Delivered",
                "Duplicates", "Status counts"],
               [stats["documents"], stats["avg_score"], stats["min_score"],
                stats["delivered"], stats["duplicates"],
                ", ".join(f"{k}: {v}" for k, v in
                          sorted(stats["by_status"].items())) or "—"]]
    t = Table(summary, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 8)]))
    story.append(t)
    story.append(Spacer(1, 6 * mm))

    rows = [["Document", "Pages", "Score", "Status", "Delivery", "Flags"]]
    for d in stats["docs"]:
        flags = ", ".join(json.loads(d["qc_detail"] or "{}").get("flags", []))
        rows.append([d["document_id"][:8], d["page_range"],
                     d["qc_score"], d["validation_status"],
                     d["delivery_status"], flags or "—"])
    t = Table(rows, hAlign="LEFT",
              colWidths=[22 * mm, 16 * mm, 14 * mm, 30 * mm, 30 * mm, None])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f5f6fa")])]))
    story.append(t)

    if include_audit_trail:
        for d in stats["docs"]:
            trail = json.loads(d["audit_trail"] or "[]")
            if not trail:
                continue
            story.append(Spacer(1, 5 * mm))
            story.append(Paragraph(
                f"Audit trail — document {d['document_id'][:8]} "
                f"(pages {d['page_range']})", styles["Heading3"]))
            rows = [["Time", "Action", "User", "Detail"]]
            for entry in trail:
                rows.append([entry.get("timestamp", "")[:19],
                             entry.get("action", ""),
                             entry.get("user", ""),
                             json.dumps(entry.get("detail", {}))[:90]])
            t = Table(rows, hAlign="LEFT",
                      colWidths=[34 * mm, 24 * mm, 20 * mm, None])
            t.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("BACKGROUND", (0, 0), (-1, 0),
                 colors.HexColor("#7f8c8d")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white)]))
            story.append(t)

    SimpleDocTemplate(str(out_path), pagesize=A4).build(story)
    return out_path
