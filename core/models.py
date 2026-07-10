"""Shared pipeline dataclasses."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


def new_id() -> str:
    return str(uuid.uuid4())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class ScanJob:
    """One captured page travelling through the pipeline."""
    batch_id: str
    project_id: str
    page_no: int
    image_path: str
    source: str = "file"                 # twain / wia / folder / file
    job_id: str = field(default_factory=new_id)
    created_at: str = field(default_factory=utc_now)


@dataclass
class PageRecord:
    """Per-page state accumulated across pipeline stages."""
    job: ScanJob
    working_path: str = ""               # enhanced/QC-fixed working copy
    ocr_text: str = ""
    ocr_confidence: float = 0.0
    barcodes: list = field(default_factory=list)
    is_blank: bool = False
    qc_score: int | None = None
    qc_grade: str = ""                   # PASS / WARN / FAIL
    qc_detail: dict = field(default_factory=dict)
    extracted_fields: dict = field(default_factory=dict)
