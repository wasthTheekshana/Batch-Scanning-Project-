"""SAST scan runner (Enhancement Plan §7 - Security Testing Scope).

Runs Bandit against the application source, writes fresh reports to
docs/security/, and prints the summary. Re-run this any time code
changes and you want an up-to-date scan - it always overwrites the
previous report rather than accumulating old ones.

Usage:
    python scripts/security_scan.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "docs" / "security"
EXCLUDE = "./.venv,./tests,./.claude,./dist,./build"


def run(fmt: str, out_name: str) -> None:
    subprocess.run([
        sys.executable, "-m", "bandit", "-r", ".",
        "-x", EXCLUDE, "-f", fmt, "-o", str(REPORT_DIR / out_name),
    ], cwd=ROOT, check=False)  # bandit exits non-zero when findings exist


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    run("json", "sast_bandit_report.json")
    run("txt", "sast_bandit_report.txt")

    summary = (REPORT_DIR / "sast_bandit_report.txt").read_text(
        encoding="utf-8")
    tail = summary[summary.rfind("Run metrics:"):]
    print(tail)
    print(f"Reports written to {REPORT_DIR}")
    print("Review docs/security/SAST_FINDINGS_REPORT.md and update it by "
          "hand if the finding set changed from what it already documents.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
