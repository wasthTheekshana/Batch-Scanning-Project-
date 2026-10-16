"""Run the test suite fast.

    python scripts/run_tests.py            # quick: skips the slow real-OCR tests, runs in parallel
    python scripts/run_tests.py --all      # everything (do this before a release build)
    python scripts/run_tests.py tests/test_search.py -k panel     # anything else goes to pytest

Needs pytest-xdist (pip install pytest-xdist); without it the tests run one by
one, which is correct but slower.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str]) -> int:
    run_all = "--all" in argv
    extra = [a for a in argv if a != "--all"]
    cmd = [sys.executable, "-m", "pytest", "-p", "no:warnings"]
    if not run_all:
        cmd += ["-m", "not slow"]
    if importlib.util.find_spec("xdist") is not None:
        cmd += ["-n", str(min(8, os.cpu_count() or 2)), "--dist", "loadfile"]
    cmd += extra
    env = {**os.environ, "QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM", "offscreen")}
    return subprocess.call(cmd, cwd=ROOT, env=env)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
