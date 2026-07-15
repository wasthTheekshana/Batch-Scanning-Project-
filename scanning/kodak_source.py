"""Kodak Scanner SDK source (HLD §11.2, Phase 2).

Wrapper skeleton around the Kodak native SDK DLL. Without the vendor
DLL (or until the SDK contract is filled in against real hardware) it
reports no devices and connect() raises ScannerUnavailable, leaving
TWAIN/WIA as the documented fallback for Kodak devices.
"""
from __future__ import annotations

from typing import List

from scanning.base import (BaseScannerSource, ScannerCapabilities,
                           ScannerUnavailable)
from scanning.vendor_sdk import load_vendor_dll

DLL_NAME = "KodakScannerSDK.dll"


class KodakSource(BaseScannerSource):
    vendor = "kodak"

    def __init__(self, dll_path: str | None = None):
        self._dll = load_vendor_dll(DLL_NAME, dll_path)
        self._device: str | None = None

    def list_devices(self) -> List[str]:
        if self._dll is None:
            return []
        # SDK enumeration call goes here once the vendor contract is
        # confirmed against real hardware (Phase 2 weeks 6-7).
        return []

    def connect(self, device_name: str) -> None:
        if self._dll is None:
            raise ScannerUnavailable(
                f"Kodak SDK not available ({DLL_NAME} missing) — "
                "use TWAIN/WIA fallback")
        self._device = device_name

    def get_capabilities(self) -> ScannerCapabilities:
        self._require_connected()
        return ScannerCapabilities(max_dpi=600, supports_duplex=True,
                                   supports_adf=True)

    def acquire(self, dpi: int, color: str, duplex: bool) -> List[bytes]:
        self._require_connected()
        raise ScannerUnavailable("Kodak SDK acquire not yet implemented")

    def disconnect(self) -> None:
        self._device = None

    def _require_connected(self) -> None:
        if self._device is None:
            raise ScannerUnavailable("not connected to a Kodak device")
