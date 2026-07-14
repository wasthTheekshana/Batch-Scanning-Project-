"""Filename template rendering (HLD §13).

Variables: {date} {barcode} {counter} / {counter:04d} {document_type}
{batch_id} {field:<name>}. Missing values render as empty; the result is
sanitised for Windows filesystems.
"""
from __future__ import annotations

import re
from datetime import datetime

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TOKEN = re.compile(r"\{(\w+)(?::(\w+))?\}|\{field:(\w+)\}")


def render(template: str, ctx: dict) -> str:
    """ctx keys: date (datetime|str), barcode, counter (int),
    document_type, batch_id, fields (dict for {field:...})."""

    def sub(match: re.Match) -> str:
        name, fmt, field_name = match.group(1), match.group(2), match.group(3)
        if field_name:  # {field:invoice_no}
            return str(ctx.get("fields", {}).get(field_name, "") or "")
        if name == "field" and fmt:  # {field:invoice_no} caught as name/fmt
            return str(ctx.get("fields", {}).get(fmt, "") or "")
        value = ctx.get(name)
        if value is None:
            return ""
        if name == "date":
            dt = value if isinstance(value, datetime) else datetime.now()
            return dt.strftime("%Y%m%d") if isinstance(value, datetime) else str(value)
        if fmt and isinstance(value, int):
            try:
                return format(value, fmt)
            except ValueError:
                return str(value)
        return str(value)

    out = _TOKEN.sub(sub, template)
    out = _UNSAFE.sub("_", out).strip(" .")
    return out or "document"
