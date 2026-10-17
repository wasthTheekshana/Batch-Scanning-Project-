"""WIA scanner source via COM interop (HLD §11.2).

Fallback for scanners without a TWAIN driver. Lazy COM initialisation;
degrades gracefully on machines without WIA devices.
"""
from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path
from typing import List

from scanning.base import (BaseScannerSource, ScannerCapabilities,
                           ScannerUnavailable)

log = logging.getLogger(__name__)

WIA_FORMAT_PNG = "{B96B3CAF-0728-11D3-9D7B-0000F81EF32E}"

# WIA property IDs (device level)
WIA_DPS_DOCUMENT_HANDLING_SELECT = "3088"
WIA_DPS_PAGES = "3096"
# WIA property IDs (scan item)
WIA_DPI_X = "6147"
WIA_DPI_Y = "6148"
WIA_START_X = "6149"
WIA_START_Y = "6150"
WIA_EXTENT_X = "6151"
WIA_EXTENT_Y = "6152"
WIA_DATA_TYPE = "4103"
WIA_BITS_PER_PIXEL = "4104"

# WIA_IPA_DATATYPE value and bit depth per colour mode
_DATA_TYPE = {"bw": (0, 1), "grayscale": (2, 8), "color": (3, 24)}
FEEDER, FLATBED, DUPLEX = 1, 2, 4          # Document Handling Select flags
FRONT_FIRST = 8                            # ... and the side to scan first
WIA_DPS_DOCUMENT_HANDLING_CAPABILITIES = "3086"
CAP_DUPLEX = 4                             # the capabilities flag for a duplex feeder
A4_INCHES = (8.27, 11.69)                  # scan area when none is configured

_PAPER_EMPTY = 0x80210003
# "another process has locked a portion of the file" (a Windows lock violation)
# and WIA's own "busy": the scanner is in use by another program, or is still
# finishing the previous scan. Both usually clear within a few seconds.
_BUSY = {0x80070021, 0x80210006}
BUSY_MESSAGE = ("The scanner is in use by another program, or is still "
                "finishing the last scan. Close any other scanning software "
                "(Kodak Smart Touch or Capture, Windows Fax and Scan, another "
                "DOK Scanner window), wait a few seconds and press Scan again.")
CONNECT_TRIES = 4
CONNECT_WAIT = 1.5      # seconds between tries

_WIA_MESSAGES = {
    0x80210001: "The scanner reported an unspecified error.",
    0x80210002: "Paper jam - clear the feeder and scan again.",
    0x80210004: "Paper problem - check the pages in the feeder.",
    0x80210005: "The scanner is offline - check the cable and power.",
    0x80210006: "The scanner is busy - wait a moment and try again.",
    0x80210007: "The scanner is warming up - try again in a moment.",
    0x80210008: "The scanner needs attention - check its display.",
    0x80210016: "The scanner cover is open - close it and scan again.",
}


def _hresult(exc: Exception) -> int | None:
    """Unsigned WIA HRESULT of a COM error, or None for anything else.
    pywin32 reports every automation failure as DISP_E_EXCEPTION and puts
    the device's own code in the exception info (args[2][5])."""
    detail = exc.args[2] if len(exc.args) > 2 else None
    if isinstance(detail, tuple) and len(detail) > 5             and isinstance(detail[5], int) and detail[5]:
        return detail[5] & 0xFFFFFFFF
    code = getattr(exc, "hresult", None)
    if code is None and exc.args and isinstance(exc.args[0], int):
        code = exc.args[0]
    return None if code is None else code & 0xFFFFFFFF


_PNG_SIGNATURE = bytes([0x89]) + b"PNG" + bytes([0x0D, 0x0A, 0x1A, 0x0A])


def _as_png(data: bytes) -> bytes:
    """Some drivers (the Kodak S2050's) hand back BMP data whatever format
    was requested; re-encode so every page is a real, compact PNG."""
    if data.startswith(_PNG_SIGNATURE):
        return data
    import cv2
    import numpy as np
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ScannerUnavailable("The scanner returned an unreadable image.")
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise ScannerUnavailable("The scanned page could not be encoded.")
    return buf.tobytes()


def _trim_background(png: bytes) -> bytes:
    """Cut the dark feeder background off the sides (and bottom) of a page.
    Some scanners (the Kodak S3060: 12 in wide) scan a window much wider than
    the paper, which sits in it off-centre; the page is the bright part. Never
    loses a page: anything unusual (no clear paper, an error) returns the
    scan unchanged."""
    try:
        import cv2
        import numpy as np
        img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
        if img is None:
            return png
        gray = img if img.ndim == 2 else cv2.cvtColor(img[..., :3], cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        bright = gray > 128
        cols = np.flatnonzero(bright.mean(axis=0) > 0.35)
        rows = np.flatnonzero(bright.mean(axis=1) > 0.35)
        if cols.size == 0 or rows.size == 0:
            return png
        x0, x1 = int(cols[0]), int(cols[-1]) + 1
        y0, y1 = int(rows[0]), int(rows[-1]) + 1
        if (x1 - x0) < 0.3 * w or (y1 - y0) < 0.3 * h:
            return png                       # not a page on a dark background
        if x0 <= 2 and y0 <= 2 and x1 >= w - 2 and y1 >= h - 2:
            return png                       # nothing to trim
        ok, buf = cv2.imencode(".png", img[y0:y1, x0:x1])
        return buf.tobytes() if ok else png
    except Exception:
        log.exception("trimming the page background failed; page kept as scanned")
        return png


def _even(pixels: float) -> int:
    return round(pixels / 2) * 2


def _snap(prop, pixels: float) -> int:
    """The nearest value the device accepts for a scan-area property. The
    allowed range (and its step: 2 at 200 dpi, 3 at 300 dpi on the Kodak
    S2050) is read from the property itself; without range information
    fall back to an even pixel count."""
    try:
        if prop.SubType == 1:                         # WIA range
            low, high, step = prop.SubTypeMin, prop.SubTypeMax, prop.SubTypeStep
            snapped = low + round((pixels - low) / step) * step
            return int(max(low, min(high, snapped)))
    except AttributeError:
        pass
    return _even(pixels)


def _full_width(prop, dpi: int) -> int:
    """The widest scan the device allows; A4 when it does not say."""
    try:
        if prop.SubType == 1:
            return int(prop.SubTypeMax)
    except AttributeError:
        pass
    return _snap(prop, A4_INCHES[0] * dpi)


def _set_props(collection, values: dict[str, object]) -> None:
    """Write each {property id: value} the device exposes, in the order
    given (not the device's own order): the scan-area limits depend on the
    resolution, so the resolution has to be written first."""
    by_id = {str(prop.PropertyID): prop for prop in collection}
    for pid, value in values.items():
        if pid in by_id:
            by_id[pid].Value = value


def _wia_device_manager():
    try:
        import win32com.client
        return win32com.client.Dispatch("WIA.DeviceManager")
    except Exception as e:
        log.debug("WIA unavailable: %s", e)
        return None


class WIASource(BaseScannerSource):
    duplex_applied: bool | None = None   # set by each scan: did two-sided scanning take?
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
        last: Exception | None = None
        for attempt in range(CONNECT_TRIES):
            try:
                for info in dm.DeviceInfos:
                    if info.Type == 1 and info.Properties("Name").Value == device_name:
                        self._device = info.Connect()
                        return
            except Exception as e:
                last = e
                if _hresult(e) in _BUSY and attempt < CONNECT_TRIES - 1:
                    time.sleep(CONNECT_WAIT)       # often clears by itself
                    continue
                if _hresult(e) in _BUSY:
                    raise ScannerUnavailable(BUSY_MESSAGE) from e
                raise ScannerUnavailable(
                    self._describe(e, "WIA connect failed")) from e
            raise ScannerUnavailable(f"WIA device not found: {device_name}")
        raise ScannerUnavailable(BUSY_MESSAGE) from last

    def get_capabilities(self) -> ScannerCapabilities:
        self._require_connected()
        duplex = False
        try:
            caps = {str(p.PropertyID): p for p in self._device.Properties}.get(
                WIA_DPS_DOCUMENT_HANDLING_CAPABILITIES)
            duplex = bool(caps is not None and int(caps.Value) & CAP_DUPLEX)
        except Exception:
            log.debug("the WIA device did not report its handling capabilities", exc_info=True)
        return ScannerCapabilities(max_dpi=600, supports_duplex=duplex,
                                   supports_adf=True)

    def acquire(self, dpi: int, color: str, duplex: bool,
                on_page=None, max_pages: int = 500) -> List[bytes]:
        """Feed every sheet in the document feeder and return one PNG per
        side. Stops when the feeder is empty; on_page(png_bytes, n) is
        called as each page arrives so the caller can show progress and
        keep pages already scanned if a later sheet jams."""
        self._require_connected()
        try:
            self._configure(dpi, color, duplex)
            item = self._device.Items(1)
            _set_props(item.Properties, {
                WIA_DPI_X: dpi, WIA_DPI_Y: dpi,
                WIA_DATA_TYPE: _DATA_TYPE.get(color, _DATA_TYPE["grayscale"])[0],
                WIA_BITS_PER_PIXEL: _DATA_TYPE.get(
                    color, _DATA_TYPE["grayscale"])[1],
            })
            # after the resolution: the valid scan-area range depends on it
            by_id = {str(prop.PropertyID): prop for prop in item.Properties}
            # the full width of the scan window, from its left edge: the paper
            # sits somewhere inside it, and the background is trimmed off each
            # page afterwards (A4 from the left edge cut the page off)
            for pid in (WIA_START_X, WIA_START_Y):
                if pid in by_id:
                    by_id[pid].Value = 0
            if WIA_EXTENT_X in by_id:
                by_id[WIA_EXTENT_X].Value = _full_width(by_id[WIA_EXTENT_X], dpi)
            if WIA_EXTENT_Y in by_id:
                by_id[WIA_EXTENT_Y].Value = _snap(
                    by_id[WIA_EXTENT_Y], A4_INCHES[1] * dpi)
        except ScannerUnavailable:
            raise
        except Exception as e:
            raise ScannerUnavailable(self._describe(e, "WIA setup failed")) from e

        pages: List[bytes] = []
        while len(pages) < max_pages:
            try:
                image = item.Transfer(WIA_FORMAT_PNG)
                with tempfile.TemporaryDirectory() as td:
                    out = Path(td) / "scan.png"
                    image.SaveFile(str(out))
                    data = _trim_background(_as_png(out.read_bytes()))
            except Exception as e:
                if _hresult(e) == _PAPER_EMPTY:
                    if pages:
                        break
                    raise ScannerUnavailable(
                        "The document feeder is empty - load paper and scan "
                        "again.") from e
                raise ScannerUnavailable(
                    self._describe(e, "WIA acquire failed")) from e
            pages.append(data)
            if on_page is not None:
                on_page(data, len(pages))
        return pages

    def _configure(self, dpi: int, color: str, duplex: bool) -> None:
        """Feeder (and duplex if asked), one sheet per Transfer. The Kodak
        S2050 driver rejects Pages=0 ("all") with 'parameter is incorrect',
        so we ask for one page per call and loop until the feeder is empty.
        Falls back to the feeder alone when the device refuses duplex."""
        props = self._device.Properties
        self.duplex_applied = None if not duplex else False
        if not duplex:
            _set_props(props, {WIA_DPS_DOCUMENT_HANDLING_SELECT: FEEDER,
                               WIA_DPS_PAGES: 1})
            return
        # some drivers want the side to start with named as well
        for mode in (FEEDER | DUPLEX, FEEDER | DUPLEX | FRONT_FIRST):
            try:
                _set_props(props, {WIA_DPS_DOCUMENT_HANDLING_SELECT: mode,
                                   WIA_DPS_PAGES: 1})
            except Exception:
                continue
            chosen = {str(p.PropertyID): p for p in props}.get(
                WIA_DPS_DOCUMENT_HANDLING_SELECT)
            try:        # trust what the device says it will do, not what we asked
                self.duplex_applied = bool(chosen is None or int(chosen.Value) & DUPLEX)
            except (TypeError, ValueError):
                self.duplex_applied = True
            if self.duplex_applied:
                return
        log.warning("the scanner would not switch to two-sided scanning; "
                    "scanning the front of each sheet only")
        _set_props(props, {WIA_DPS_DOCUMENT_HANDLING_SELECT: FEEDER,
                           WIA_DPS_PAGES: 1})

    @staticmethod
    def _describe(exc: Exception, prefix: str) -> str:
        code = _hresult(exc)
        if code in _WIA_MESSAGES:
            return _WIA_MESSAGES[code]
        # the error text of a COM failure names a device/driver, never a
        # document, so it is safe to show
        return f"{prefix}: {exc}"

    def disconnect(self) -> None:
        self._device = None

    def _require_connected(self) -> None:
        if self._device is None:
            raise ScannerUnavailable("not connected to a WIA device")
