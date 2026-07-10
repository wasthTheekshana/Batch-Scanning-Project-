"""QC stage plugin interface.

Each of the five QC stages implements BaseQCStage; new stages drop into
qc/ without pipeline changes. Stages receive a working copy of the page
image plus a context dict (project qc config, dpi, prior results) and
return a QCStageResult with the (possibly fixed) image and deduction.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np


@dataclass
class QCStageResult:
    stage: str
    deduction: int = 0
    fixed: bool = False
    auto_fail: bool = False
    flags: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)
    image: Optional[np.ndarray] = None  # fixed image; None = unchanged


class BaseQCStage(ABC):
    """All QC stages implement this interface."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stage key as used in project qc.stages config."""

    @abstractmethod
    def run(self, image: np.ndarray, context: dict) -> QCStageResult: ...
