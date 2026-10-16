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


EMPTY_INK = 0.004   # ink share under which a page is empty, not just sparse


@dataclass
class PageRecord:
    """Per-page state accumulated across pipeline stages."""
    job: ScanJob
    working_path: str = ""               # enhanced/QC-fixed working copy
    ocr_text: str = ""
    ocr_confidence: float = 0.0
    ocr_words: list = field(default_factory=list)   # word boxes, to align forms
    barcodes: list = field(default_factory=list)
    is_blank: bool = False
    ink_density: float = 1.0             # share of dark pixels (0 = empty page)
    is_custody_label: bool = False
    custody_codes: list = field(default_factory=list)   # custody QR values seen on the page
    custody_unknown: bool = False        # one of them belongs to no bundle here
    rotation: int = 0                    # clockwise degrees that make the page upright
    doc_type: str | None = None          # project document type of this page
    qc_score: int | None = None
    qc_grade: str = ""                   # PASS / WARN / FAIL
    qc_detail: dict = field(default_factory=dict)
    extracted_fields: dict = field(default_factory=dict)
    low_conf_fields: list = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """Truly empty (scanner noise only): far stricter than is_blank, which
        also catches sparse pages such as an ID card or a short note."""
        return self.ink_density < EMPTY_INK
