"""Scanner abstraction layer (HLD §11.1).

All input sources implement BaseScannerSource so vendor SDKs can be added
in Phase 2 without touching the pipeline.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List


class ScannerUnavailable(Exception):
    """Raised when a scanner driver stack is not present on this machine."""


@dataclass
class ScannerCapabilities:
    max_dpi: int
    supports_duplex: bool
    supports_adf: bool
    color_modes: List[str] = field(default_factory=lambda: ["bw", "grayscale", "color"])


class BaseScannerSource(ABC):
    """All scanner drivers implement this interface."""

    @abstractmethod
    def list_devices(self) -> List[str]: ...

    @abstractmethod
    def connect(self, device_name: str) -> None: ...

    @abstractmethod
    def get_capabilities(self) -> ScannerCapabilities: ...

    @abstractmethod
    def acquire(self, dpi: int, color: str, duplex: bool) -> List[bytes]:
        """Scan and return raw image bytes (PNG/TIFF), one per side."""

    @abstractmethod
    def disconnect(self) -> None: ...
