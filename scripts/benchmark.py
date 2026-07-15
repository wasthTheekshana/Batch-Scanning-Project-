"""Performance benchmark suite (HLD §18 Phase 3 weeks 11-12, §19 NFRs).

Runs N synthetic pages through the real pipeline (imaging + QC + rules +
export + local delivery) and reports throughput against the HLD targets:
QC ≥ 20 pages/min, export ≥ 30 pages/min, OCR 1,000+ pages/hour.

Usage:
    python scripts/benchmark.py --pages 100          # no OCR
    python scripts/benchmark.py --pages 100 --ocr    # with Tesseract
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402

from core.job_queue import Pipeline  # noqa: E402
from core.models import ScanJob  # noqa: E402
from data.db import Database  # noqa: E402
from delivery.local_folder import LocalFolderChannel  # noqa: E402
from export.batch_exporter import export_document  # noqa: E402
from tests.conftest import make_text_page  # noqa: E402
from tests.test_pipeline import make_profile  # noqa: E402


def run_benchmark(pages: int, use_ocr: bool,
                  work_dir: Path | None = None) -> dict:
    work = Path(work_dir or tempfile.mkdtemp(prefix="dok_bench_"))
    db = Database(work / "bench.db")
    profile = make_profile(work)
    profile["qc"]["stages"] = ["punch_hole", "rotation", "margin",
                               "color", "advanced"]
    ocr_fn = None
    if use_ocr:
        from ocr.tesseract_engine import is_available, ocr_page
        if not is_available():
            print("WARNING: tesseract not found — running without OCR")
        else:
            ocr_fn = ocr_page

    stage_times: dict[str, float] = {}
    stage_counts: dict[str, int] = {}
    t0 = time.perf_counter()

    def on_event(event):
        if event.get("status") == "done":
            stage = event["stage"]
            stage_times[stage] = time.perf_counter() - t0
            stage_counts[stage] = stage_counts.get(stage, 0) + 1

    channel = LocalFolderChannel(profile["delivery"]["local_folder"])
    pipeline = Pipeline(db, profile, [channel], export_document,
                        ocr_fn=ocr_fn, working_dir=work / "staging")
    # rotation stage needs OSD; keep it deterministic without tesseract
    pipeline.subscribe(on_event)

    print(f"generating {pages} synthetic pages…")
    page_img = make_text_page()
    jobs = []
    for i in range(1, pages + 1):
        p = work / f"bench_{i:05d}.png"
        cv2.imwrite(str(p), page_img)
        jobs.append(ScanJob(batch_id="BENCH", project_id=profile["project_id"],
                            page_no=i, image_path=str(p)))

    print("running pipeline…")
    pipeline.start()
    start = time.perf_counter()
    pipeline.submit_batch("BENCH", jobs)
    deadline = start + max(600, pages * 6)
    while time.perf_counter() < deadline:
        docs = db.list_documents("BENCH")
        if docs and all(d["delivery_status"] != "pending" or
                        d["validation_status"] != "auto_approved"
                        for d in docs):
            break
        time.sleep(0.25)
    elapsed = time.perf_counter() - start
    pipeline.shutdown()

    pages_per_min = pages / elapsed * 60
    result = {"pages": pages, "ocr": ocr_fn is not None,
              "elapsed_s": round(elapsed, 1),
              "pages_per_minute": round(pages_per_min, 1),
              "pages_per_hour": round(pages_per_min * 60),
              "stage_counts": stage_counts}
    db.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, default=50)
    parser.add_argument("--ocr", action="store_true")
    args = parser.parse_args()

    result = run_benchmark(args.pages, args.ocr)

    print("\n=== DOK Scanner benchmark ===")
    print(f"pages processed : {result['pages']} (OCR: {result['ocr']})")
    print(f"elapsed         : {result['elapsed_s']} s")
    print(f"throughput      : {result['pages_per_minute']} pages/min "
          f"({result['pages_per_hour']} pages/hour)")
    print("\nHLD 19 targets  : QC >= 20 pages/min | export >= 30 pages/min "
          "| OCR 1,000-3,000 pages/hour")
    verdict = "MEETS" if result["pages_per_minute"] >= 20 else "BELOW"
    print(f"end-to-end vs QC target: {verdict} "
          f"({result['pages_per_minute']} vs 20 pages/min)")


if __name__ == "__main__":
    main()
