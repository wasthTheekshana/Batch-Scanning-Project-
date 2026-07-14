"""File import source (HLD §4 Layer 1).

Accepts PDF, TIFF, JPG, PNG, BMP dropped into the scan wizard or hot
folder. Multi-page PDFs and TIFFs are exploded to per-page PNGs in the
staging directory so the pipeline always sees single-page images.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

from core.models import ScanJob

log = logging.getLogger(__name__)

SUPPORTED = {".pdf", ".tif", ".tiff", ".jpg", ".jpeg", ".png", ".bmp"}


def import_files(paths: list[str | Path], batch_id: str, project_id: str,
                 staging_dir: str | Path) -> list[ScanJob]:
    """Explode input files into per-page PNGs and wrap them in ScanJobs.
    Unsupported extensions are skipped with a warning."""
    staging = Path(staging_dir)
    staging.mkdir(parents=True, exist_ok=True)
    jobs: list[ScanJob] = []
    page_no = 0
    for raw in paths:
        path = Path(raw)
        ext = path.suffix.lower()
        if ext not in SUPPORTED:
            log.warning("skipping unsupported file: %s", path)
            continue
        if ext == ".pdf":
            pages = _explode_pdf(path, staging, batch_id, page_no)
        elif ext in (".tif", ".tiff"):
            pages = _explode_tiff(path, staging, batch_id, page_no)
        else:
            out = staging / f"{batch_id}_p{page_no + 1:04d}.png"
            with Image.open(path) as im:
                im.convert("RGB" if im.mode not in ("L", "RGB") else im.mode
                           ).save(out)
            pages = [out]
        for p in pages:
            page_no += 1
            jobs.append(ScanJob(batch_id=batch_id, project_id=project_id,
                                page_no=page_no, image_path=str(p),
                                source="file"))
    return jobs


def _explode_tiff(path: Path, staging: Path, batch_id: str,
                  offset: int) -> list[Path]:
    outputs = []
    with Image.open(path) as im:
        for i in range(getattr(im, "n_frames", 1)):
            im.seek(i)
            out = staging / f"{batch_id}_p{offset + i + 1:04d}.png"
            frame = im.convert("L") if im.mode == "1" else im.copy()
            frame.save(out)
            outputs.append(out)
    return outputs


def _explode_pdf(path: Path, staging: Path, batch_id: str,
                 offset: int) -> list[Path]:
    """Rasterise PDF pages. Uses PyMuPDF when present; otherwise the file
    is skipped with a warning (offline constraint: no external tools)."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        log.warning("PyMuPDF not installed — cannot import PDF %s", path)
        return []
    outputs = []
    doc = fitz.open(str(path))
    try:
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=300)
            out = staging / f"{batch_id}_p{offset + i + 1:04d}.png"
            pix.save(str(out))
            outputs.append(out)
    finally:
        doc.close()
    return outputs
