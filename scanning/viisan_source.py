"""Viisan SDK source (HLD §11.2, Phase 2). Same degradation contract as
KodakSource: no DLL → no devices → TWAIN/WIA fallback."""
from __future__ import annotations

from typing import List

from scanning.base import (BaseScannerSource, ScannerCapabilities,
                           ScannerUnavailable)
from scanning.vendor_sdk import load_vendor_dll

DLL_NAME = "ViisanSDK.dll"


class ViisanSource(BaseScannerSource):
    vendor = "viisan"

    def __init__(self, dll_path: str | None = None):
        self._dll = load_vendor_dll(DLL_NAME, dll_path)
        self._device: str | None = None

    def list_devices(self) -> List[str]:
        return [] if self._dll is None else []

    def connect(self, device_name: str) -> None:
        if self._dll is None:
            raise ScannerUnavailable(
                f"Viisan SDK not available ({DLL_NAME} missing) — "
                "use TWAIN/WIA fallback")
        self._device = device_name

    def get_capabilities(self) -> ScannerCapabilities:
        self._require_connected()
        return ScannerCapabilities(max_dpi=400, supports_duplex=False,
                                   supports_adf=False)

    def acquire(self, dpi: int, color: str, duplex: bool) -> List[bytes]:
        self._require_connected()
        raise ScannerUnavailable("Viisan SDK acquire not yet implemented")

    def disconnect(self) -> None:
        self._device = None

    def _require_connected(self) -> None:
        if self._device is None:
            raise ScannerUnavailable("not connected to a Viisan device")
