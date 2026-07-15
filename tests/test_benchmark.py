"""Smoke test for the benchmark harness (no absolute target in CI)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from benchmark import run_benchmark  # noqa: E402


def test_benchmark_runs_and_reports(tmp_path):
    result = run_benchmark(pages=10, use_ocr=False, work_dir=tmp_path)
    assert result["pages"] == 10
    assert result["pages_per_minute"] > 0
    assert result["stage_counts"].get("qc", 0) == 10
    assert result["stage_counts"].get("delivery", 0) >= 1
