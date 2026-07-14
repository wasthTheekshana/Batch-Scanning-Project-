"""Run the enabled QC stages in configured order on a working copy
(the original image is never touched — HLD §5 DESIGN note)."""
from __future__ import annotations

import numpy as np

from qc.advanced import AdvancedStage
from qc.base import BaseQCStage
from qc.color import ColorStage
from qc.margin import MarginStage
from qc.punch_hole import PunchHoleStage
from qc.rotation import RotationStage
from qc.scorer import QCVerdict, score_page

STAGE_REGISTRY: dict[str, BaseQCStage] = {
    s.name: s for s in (PunchHoleStage(), RotationStage(), MarginStage(),
                        ColorStage(), AdvancedStage())
}


def run_qc(image: np.ndarray, qc_config: dict, context: dict | None = None
           ) -> tuple[np.ndarray, QCVerdict]:
    """Returns (fixed_working_copy, verdict). Honours qc.enabled,
    qc.stages ordering, and per-document skip_stages from the rule engine."""
    ctx = {"config": qc_config, **(context or {})}
    working = image.copy()

    if not qc_config.get("enabled", True):
        return working, QCVerdict(score=100, grade="PASS",
                                  detail={"skipped": "qc.disabled"})

    skip = set(ctx.get("skip_stages", []))
    results = []
    for stage_name in qc_config.get("stages", list(STAGE_REGISTRY)):
        if stage_name in skip or stage_name not in STAGE_REGISTRY:
            continue
        result = STAGE_REGISTRY[stage_name].run(working, ctx)
        if result.image is not None:
            working = result.image
        results.append(result)

    verdict = score_page(results, pass_score=qc_config.get("pass_score", 80))
    return working, verdict
