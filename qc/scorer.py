"""QC score engine — per-page routing (HLD §5).

Start at 100, subtract every stage deduction, clamp to 0–100.
PASS ≥ pass_score (default 80) → export queue; WARN 60–79 and FAIL ≤ 59
→ review queue. Auto-fail conditions (blur below fail threshold, DPI
below fail floor) force FAIL regardless of arithmetic.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from qc.base import QCStageResult

WARN_FLOOR = 60


@dataclass
class QCVerdict:
    score: int
    grade: str                      # PASS / WARN / FAIL
    flags: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)


def score_page(results: list[QCStageResult], pass_score: int = 80) -> QCVerdict:
    score = 100
    flags: list[str] = []
    detail: dict = {}
    forced_fail = False
    for r in results:
        score -= r.deduction
        flags.extend(r.flags)
        detail[r.stage] = {"deduction": r.deduction, "fixed": r.fixed,
                           **r.detail}
        forced_fail = forced_fail or r.auto_fail
    score = max(0, min(100, score))

    if forced_fail or score < WARN_FLOOR:
        grade = "FAIL"
    elif score >= pass_score:
        grade = "PASS"
    else:
        grade = "WARN"
    return QCVerdict(score=score, grade=grade, flags=flags, detail=detail)
