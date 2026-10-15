"""Index files: the index fields that go with each exported PDF.

A bank wants more than the PDF: the customer id, NIC, account number, dates
and so on, in a file a downstream system can load. One file is written next to
every PDF (same base name: .csv, .xml or .json) and optionally every document
is also appended to a running index.csv in the output folder.

All values are untrusted text (they came from OCR or an operator), so every
format is written safely: CSV cells that a spreadsheet would run as a formula
are neutralised, XML is generated (never assembled from strings), and nothing
here reads a path from a value.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

FORMATS = ("csv", "xml", "json")
EXTENSIONS = {"csv": ".csv", "xml": ".xml", "json": ".json"}
FIXED_COLUMNS = ["document_id", "project", "batch_id", "barcode", "page_count",
                 "document_types", "qc_score", "scanned_at", "operator",
                 "pdf_file", "pdf_sha256"]
RUNNING_NAME = "index.csv"

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_ELEMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_running_lock = threading.Lock()
LOCK_WAIT = 15.0        # seconds to wait for another writer of the running index
LOCK_STALE = 60.0       # a lock file older than this was left by a crashed writer


class IndexLockTimeout(Exception):
    """Another process held the running index for too long."""


class _FolderLock:
    """A lock between PROCESSES (two PCs delivering into one shared folder):
    a lock file created exclusively beside index.csv. The threading lock only
    covers one process, so without this a second writer could read the file
    while the first rewrites it and one document's row would be lost."""

    def __init__(self, path: Path):
        self.path = path.with_name(path.name + ".lock")

    def __enter__(self):
        deadline = time.monotonic() + LOCK_WAIT
        while True:
            try:
                handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(handle)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > LOCK_STALE:
                        self.path.unlink(missing_ok=True)      # a crashed writer's
                        continue
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise IndexLockTimeout(self.path.name)
                time.sleep(0.05)

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def _text(value) -> str:
    return "" if value is None else str(value)


def _sha256(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


def build_record(*, document_id: str, project_name: str, batch_id: str,
                 operator: str, scanned_at: str, qc_score, page_count: int,
                 pdf_path: str | Path, fields: dict) -> dict:
    """One ordered record: the fixed columns, then each remaining index field.
    A field can never replace a fixed column."""
    pdf_path = Path(pdf_path)
    fields = dict(fields or {})
    record = {
        "document_id": document_id,
        "project": project_name,
        "batch_id": batch_id,
        "barcode": _text(fields.pop("barcode", "")),
        "page_count": page_count,
        "document_types": _text(fields.pop("doc_types", "")),
        "qc_score": qc_score,
        "scanned_at": scanned_at,
        "operator": operator,
        "pdf_file": pdf_path.name,
        "pdf_sha256": _sha256(pdf_path),
    }
    for name, value in fields.items():
        if name not in record:
            record[str(name)] = _text(value)
    return {k: (v if isinstance(v, (int, float)) and not isinstance(v, bool)
                and k in ("page_count", "qc_score") else _text(v))
            for k, v in record.items()}


def _safe_cell(value: str) -> str:
    """Neutralise a CSV cell a spreadsheet would execute as a formula."""
    return "'" + value if value.startswith(_FORMULA_START) else value


def _csv_text(columns: list[str], rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_safe_cell(_text(row.get(c, ""))) for c in columns])
    return buffer.getvalue()


def _xml_text(record: dict) -> str:
    root = ET.Element("document")
    for name, value in record.items():
        if _ELEMENT_NAME.match(name) and name != "field":
            ET.SubElement(root, name).text = _text(value)
        else:                       # not usable as an element name: keep it as data
            ET.SubElement(root, "field", {"name": name}).text = _text(value)
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


def render(record: dict, fmt: str) -> str:
    if fmt == "csv":
        return _csv_text(list(record), [record])
    if fmt == "xml":
        return _xml_text(record)
    if fmt == "json":
        return json.dumps(record, ensure_ascii=False, indent=2) + "\n"
    raise ValueError(f"unknown index format {fmt!r} (use one of {FORMATS})")


def _write_atomic(path: Path, text: str, encoding: str) -> None:
    """Write beside the target and rename over it, so a reader (or a delivery
    that starts at the same moment) never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding=encoding, newline="") as f:
            f.write(text)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def write_index(pdf_path: str | Path, record: dict, fmt: str) -> Path:
    """The index file for one PDF, next to it. Replaces an earlier version."""
    text = render(record, fmt)                     # refuses an unknown format first
    out = Path(pdf_path).with_suffix(EXTENSIONS[fmt])
    _write_atomic(out, text, "utf-8-sig" if fmt == "csv" else "utf-8")
    return out


def append_running_csv(folder: str | Path, record: dict) -> Path:
    """Add the document to index.csv in `folder`. A document already listed
    (same document_id) has its row replaced, so a correction does not
    duplicate it; a field the file has not seen widens the header."""
    path = Path(folder) / RUNNING_NAME
    Path(folder).mkdir(parents=True, exist_ok=True)
    with _running_lock, _FolderLock(path):
        columns: list[str] = []
        rows: list[dict] = []
        if path.exists():
            with open(path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                columns = list(reader.fieldnames or [])
                rows = list(reader)
        for name in record:
            if name not in columns:
                columns.append(name)
        rows = [r for r in rows if r.get("document_id") != record["document_id"]]
        rows.append({k: _text(v) for k, v in record.items()})
        _write_atomic(path, _csv_text(columns, rows), "utf-8-sig")
    return path
