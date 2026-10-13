"""Background export queue with retry (HLD §13, §19 reliability).

Exports run in a ThreadPoolExecutor so the operator can keep scanning.
Failed exports retry 3× before the document is flagged for the operator.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from core.safe_paths import contained_file, safe_join
from ocr.doc_types import type_runs
from export.filename import render
from export.pdf_writer import write_image_pdf, write_searchable_pdf
from export.tiff_writer import write_multipage_tiff

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


class ExportError(Exception):
    """Export failed after all retries."""


def export_document(images: list[str], export_cfg: dict, ctx: dict,
                    ocr_texts: list[str] | None = None) -> Path:
    """Synchronous single-document export honouring project export config.
    ctx feeds the filename template (date/barcode/counter/fields/...)."""
    fmt = export_cfg.get("format", "searchable_pdf")
    name = render(export_cfg.get("filename_template", "{batch_id}_{counter:04d}"), ctx)
    out_dir = Path(export_cfg.get("output_path") or ".")
    if sub := ctx.get("output_folder"):
        out_dir = safe_join(out_dir, sub)
    if fmt == "tiff":
        return write_multipage_tiff(images, contained_file(out_dir, f"{name}.tif"))
    if fmt == "image_pdf":
        path = write_image_pdf(images, contained_file(out_dir, f"{name}.pdf"))
    else:
        # searchable_pdf / pdf_a both go through reportlab
        path = write_searchable_pdf(images, ocr_texts or [""] * len(images),
                                    contained_file(out_dir, f"{name}.pdf"),
                                    dpi=ctx.get("dpi", 300))
    add_type_bookmarks(path, ctx.get("page_types"))
    return path


def add_type_bookmarks(pdf_path: Path, page_types: list | None) -> None:
    """One bookmark per stretch of pages of the same document type (NIC,
    Application, ...), so a customer's single PDF can be navigated by type.
    Bookmarks are a convenience: a failure never fails the export."""
    runs = type_runs(page_types or [])
    if not runs:
        return
    try:
        import fitz
        with fitz.open(str(pdf_path)) as pdf:
            pdf.set_toc([[1, name, index + 1] for index, name in runs
                         if index < pdf.page_count])
            pdf.saveIncr()
    except Exception:
        log.warning("could not add bookmarks to %s", pdf_path.name)


class BatchExporter:
    def __init__(self, executor: ThreadPoolExecutor | None = None,
                 max_attempts: int = MAX_ATTEMPTS,
                 retry_delay: float = 0.5):
        self._executor = executor or ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="export")
        self._own_executor = executor is None
        self.max_attempts = max_attempts
        self.retry_delay = retry_delay

    def submit(self, images: list[str], export_cfg: dict, ctx: dict,
               ocr_texts: list[str] | None = None,
               export_fn=export_document) -> Future:
        return self._executor.submit(self._run_with_retry, export_fn,
                                     images, export_cfg, ctx, ocr_texts)

    def _run_with_retry(self, export_fn, images, export_cfg, ctx, ocr_texts):
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return export_fn(images, export_cfg, ctx, ocr_texts)
            except Exception as e:
                last_error = e
                log.warning("export attempt %d/%d failed: %s",
                            attempt, self.max_attempts, e)
                if attempt < self.max_attempts:
                    time.sleep(self.retry_delay)
        raise ExportError(str(last_error))

    def shutdown(self, wait: bool = True) -> None:
        if self._own_executor:
            self._executor.shutdown(wait=wait)
