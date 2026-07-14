"""TWAIN 2.x scanner source (HLD §11.2).

Lazy import: machines without a TWAIN driver stack (or the twain
package) still run — list_devices() returns [] and connect() raises
ScannerUnavailable, so the UI falls back to WIA or file import.
"""
from __future__ import annotations

import logging
from typing import List

from scanning.base import (BaseScannerSource, ScannerCapabilities,
                           ScannerUnavailable)

log = logging.getLogger(__name__)


def _twain():
    try:
        import twain
        return twain
    except Exception as e:
        log.debug("twain module unavailable: %s", e)
        return None


class TWAINSource(BaseScannerSource):
    def __init__(self):
        self._sm = None
        self._source = None

    def list_devices(self) -> List[str]:
        tw = _twain()
        if tw is None:
            return []
        try:
            sm = tw.SourceManager(0)
            names = list(sm.GetSourceList())
            sm.destroy()
            return names
        except Exception as e:
            log.warning("TWAIN device enumeration failed: %s", e)
            return []

    def connect(self, device_name: str) -> None:
        tw = _twain()
        if tw is None:
            raise ScannerUnavailable("TWAIN driver stack not available")
        try:
            self._sm = tw.SourceManager(0)
            self._source = self._sm.OpenSource(device_name)
        except Exception as e:
            raise ScannerUnavailable(f"cannot open TWAIN source: {e}") from e

    def get_capabilities(self) -> ScannerCapabilities:
        self._require_connected()
        try:
            max_dpi = int(self._source.GetCapability(
                _twain().ICAP_XRESOLUTION)[1][3][-1])
        except Exception:
            max_dpi = 600
        return ScannerCapabilities(max_dpi=max_dpi, supports_duplex=True,
                                   supports_adf=True)

    def acquire(self, dpi: int, color: str, duplex: bool) -> List[bytes]:
        self._require_connected()
        tw = _twain()
        pixel_types = {"bw": tw.TWPT_BW, "grayscale": tw.TWPT_GRAY,
                       "color": tw.TWPT_RGB}
        try:
            self._source.SetCapability(
                tw.ICAP_XRESOLUTION, tw.TWTY_FIX32, float(dpi))
            self._source.SetCapability(
                tw.ICAP_YRESOLUTION, tw.TWTY_FIX32, float(dpi))
            self._source.SetCapability(
                tw.ICAP_PIXELTYPE, tw.TWTY_UINT16,
                pixel_types.get(color, tw.TWPT_GRAY))
            if duplex:
                self._source.SetCapability(
                    tw.CAP_DUPLEXENABLED, tw.TWTY_BOOL, True)
            self._source.RequestAcquire(0, 0)
            pages: List[bytes] = []
            while True:
                try:
                    (handle, remaining) = self._source.XferImageNatively()
                except tw.excDSTransferCancelled:
                    break
                pages.append(tw.DIBToBMFile(handle))
                tw.GlobalHandleFree(handle)
                if remaining == 0:
                    break
            return pages
        except ScannerUnavailable:
            raise
        except Exception as e:
            raise ScannerUnavailable(f"TWAIN acquire failed: {e}") from e

    def disconnect(self) -> None:
        for obj in (self._source, self._sm):
            try:
                if obj is not None:
                    obj.destroy()
            except Exception:
                pass
        self._source = self._sm = None

    def _require_connected(self) -> None:
        if self._source is None:
            raise ScannerUnavailable("not connected to a TWAIN device")
