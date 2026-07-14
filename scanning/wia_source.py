"""WIA scanner source via COM interop (HLD §11.2).

Fallback for scanners without a TWAIN driver. Lazy COM initialisation;
degrades gracefully on machines without WIA devices.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import List

from scanning.base import (BaseScannerSource, ScannerCapabilities,
                           ScannerUnavailable)

log = logging.getLogger(__name__)

WIA_FORMAT_PNG = "{B96B3CAF-0728-11D3-9D7B-0000F81EF32E}"

# WIA property IDs
WIA_DPI_X = "6147"
WIA_DPI_Y = "6148"
WIA_COLOR_MODE = "6146"  # 0=color? per WIA_IPA_DATATYPE: 1=BW,2=gray,3=color


def _wia_device_manager():
    try:
        import win32com.client
        return win32com.client.Dispatch("WIA.DeviceManager")
    except Exception as e:
        log.debug("WIA unavailable: %s", e)
        return None


class WIASource(BaseScannerSource):
    def __init__(self):
        self._device = None

    def list_devices(self) -> List[str]:
        dm = _wia_device_manager()
        if dm is None:
            return []
        try:
            return [info.Properties("Name").Value
                    for info in dm.DeviceInfos if info.Type == 1]
        except Exception as e:
            log.warning("WIA enumeration failed: %s", e)
            return []

    def connect(self, device_name: str) -> None:
        dm = _wia_device_manager()
        if dm is None:
            raise ScannerUnavailable("WIA not available on this machine")
        try:
            for info in dm.DeviceInfos:
                if info.Type == 1 and info.Properties("Name").Value == device_name:
                    self._device = info.Connect()
                    return
        except Exception as e:
            raise ScannerUnavailable(f"WIA connect failed: {e}") from e
        raise ScannerUnavailable(f"WIA device not found: {device_name}")

    def get_capabilities(self) -> ScannerCapabilities:
        self._require_connected()
        return ScannerCapabilities(max_dpi=600, supports_duplex=False,
                                   supports_adf=True)

    def acquire(self, dpi: int, color: str, duplex: bool) -> List[bytes]:
        self._require_connected()
        color_map = {"bw": 1, "grayscale": 2, "color": 3}
        try:
            item = self._device.Items(1)
            for prop in item.Properties:
                pid = str(prop.PropertyID)
                if pid in (WIA_DPI_X, WIA_DPI_Y):
                    prop.Value = dpi
                elif pid == WIA_COLOR_MODE:
                    prop.Value = color_map.get(color, 2)
            image = item.Transfer(WIA_FORMAT_PNG)
            with tempfile.TemporaryDirectory() as td:
                out = Path(td) / "scan.png"
                image.SaveFile(str(out))
                return [out.read_bytes()]
        except ScannerUnavailable:
            raise
        except Exception as e:
            raise ScannerUnavailable(f"WIA acquire failed: {e}") from e

    def disconnect(self) -> None:
        self._device = None

    def _require_connected(self) -> None:
        if self._device is None:
            raise ScannerUnavailable("not connected to a WIA device")
