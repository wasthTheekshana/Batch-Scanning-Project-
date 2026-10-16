"""TWAIN 2.x scanner source (HLD §11.2).

The scanner vendor's own driver: it feeds continuously and delivers both sides of
every sheet, including the last one. (Through WIA the Kodak S3060 restarts a scan
job per page, about 4 seconds each, and loses most backs.) Measured on the real
scanner: 5 two-sided sheets in 16 seconds, all 10 sides.

Lazy import: machines without a TWAIN driver stack (or the twain package) still
run: list_devices() returns [] and connect() raises ScannerUnavailable, so the UI
falls back to WIA or file import.
"""
from __future__ import annotations

import ctypes
import logging
import threading
from typing import Callable, List

import cv2
import numpy as np

from scanning.base import (BaseScannerSource, ScannerCapabilities,
                           ScannerUnavailable)

log = logging.getLogger(__name__)

WM_QUIT = 0x0012
IDLE_TIMEOUT_S = 120.0        # no image for this long: stop cleanly (a stuck driver)

EMPTY_MESSAGE = "The document feeder is empty - load paper and scan again."
BUSY_MESSAGE = ("The scanner is in use by another program, or is still finishing the "
                "last scan. Close any other scanning software, wait a few seconds and "
                "press Scan again.")
NO_DRIVER_MESSAGE = ("The scanner's TWAIN driver could not be opened. Switch the scanner "
                     "on, check it is connected, and try again (or choose its other "
                     "entry in the list).")


def _twain():
    try:
        import twain
        return twain
    except Exception as e:
        log.debug("twain module unavailable: %s", e)
        return None


def _hidden_window() -> int:
    """A window for the driver to post its 'image ready' messages to. Without
    one TWAIN waits for ever. 0 when this cannot be made (no pywin32)."""
    try:
        import win32gui
        return win32gui.CreateWindow("STATIC", "dok-twain", 0, 0, 0, 0, 0, 0, 0, 0, None)
    except Exception as e:
        log.debug("no hidden window for TWAIN: %s", e)
        return 0


def _destroy_window(hwnd: int) -> None:
    if hwnd:
        try:
            import win32gui
            win32gui.DestroyWindow(hwnd)
        except Exception:
            pass


def _png(bmp: bytes) -> bytes:
    """A real, compact PNG from the BMP the driver hands over."""
    img = cv2.imdecode(np.frombuffer(bmp, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ScannerUnavailable("The scanner returned an unreadable image.")
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise ScannerUnavailable("The scanned page could not be encoded.")
    return buf.tobytes()


class TWAINSource(BaseScannerSource):
    duplex_applied: bool | None = None    # set by each scan: did two-sided scanning switch on?

    def __init__(self):
        self._sm = None
        self._source = None
        self._hwnd = 0

    # -- devices -----------------------------------------------------------------
    def list_devices(self) -> List[str]:
        tw = _twain()
        if tw is None:
            return []
        hwnd = _hidden_window()
        try:
            sm = tw.SourceManager(hwnd)
            names = list(sm.GetSourceList())
            sm.destroy()
            return names
        except Exception as e:
            log.warning("TWAIN device enumeration failed: %s", e)
            return []
        finally:
            _destroy_window(hwnd)

    def connect(self, device_name: str) -> None:
        tw = _twain()
        if tw is None:
            raise ScannerUnavailable("TWAIN driver stack not available")
        self._hwnd = _hidden_window()
        try:
            self._sm = tw.SourceManager(self._hwnd)
            self._source = self._sm.OpenSource(device_name)
        except Exception as e:
            self.disconnect()
            log.warning("cannot open TWAIN source: %s", type(e).__name__)
            raise ScannerUnavailable(NO_DRIVER_MESSAGE) from e

    def get_capabilities(self) -> ScannerCapabilities:
        self._require_connected()
        tw = _twain()
        duplex = False
        try:
            duplex = int(self._source.GetCapability(tw.CAP_DUPLEX)[1]) > 0
        except Exception:
            pass
        return ScannerCapabilities(max_dpi=600, supports_duplex=duplex, supports_adf=True)

    # -- scanning ----------------------------------------------------------------
    def _set(self, label: str, code, kind, value) -> bool:
        try:
            self._source.SetCapability(code, kind, value)
            return True
        except Exception as e:
            log.warning("TWAIN: could not set %s (%s)", label, type(e).__name__)
            return False

    def acquire(self, dpi: int, color: str, duplex: bool,
                on_page: Callable[[bytes, int], None] | None = None,
                max_pages: int = 500, idle_timeout: float = IDLE_TIMEOUT_S) -> List[bytes]:
        """Feed every sheet in the document feeder and return one PNG per side,
        in order. on_page(png_bytes, n) is called as each page arrives so the
        caller can show progress and keep the pages already scanned if a later
        sheet jams."""
        self._require_connected()
        tw = _twain()
        pixel_types = {"bw": tw.TWPT_BW, "grayscale": tw.TWPT_GRAY, "color": tw.TWPT_RGB}
        self._set("resolution", tw.ICAP_XRESOLUTION, tw.TWTY_FIX32, float(dpi))
        self._set("resolution", tw.ICAP_YRESOLUTION, tw.TWTY_FIX32, float(dpi))
        self._set("colour mode", tw.ICAP_PIXELTYPE, tw.TWTY_UINT16,
                  pixel_types.get(color, tw.TWPT_GRAY))
        self._set("feeder", tw.CAP_FEEDERENABLED, tw.TWTY_BOOL, True)
        self.duplex_applied = (self._set("two-sided", tw.CAP_DUPLEXENABLED, tw.TWTY_BOOL, True)
                               if duplex else None)
        if not duplex:
            self._set("one-sided", tw.CAP_DUPLEXENABLED, tw.TWTY_BOOL, False)
        self._set("all pages", tw.CAP_XFERCOUNT, tw.TWTY_INT16, -1)

        pages: List[bytes] = []
        failure: list[Exception] = []
        main_thread = ctypes.windll.kernel32.GetCurrentThreadId() if hasattr(ctypes, "windll") else 0
        timer: list[threading.Timer] = []

        def arm():                     # a driver that goes quiet is stopped, never left hanging
            for t in timer:
                t.cancel()
            t = threading.Timer(idle_timeout, lambda: ctypes.windll.user32.PostThreadMessageW(
                main_thread, WM_QUIT, 0, 0))
            t.daemon = True
            t.start()
            timer[:] = [t]

        def after(image, remaining):
            import os
            import tempfile
            fd, path = tempfile.mkstemp(suffix=".bmp")
            os.close(fd)
            try:
                image.save(path)
                with open(path, "rb") as f:
                    data = _png(f.read())
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            pages.append(data)
            arm()
            if on_page is not None:
                on_page(data, len(pages))
            if len(pages) >= max_pages:
                raise tw.CancelAll()

        arm()
        try:
            self._source.acquire_natively(after, show_ui=False)
        except ScannerUnavailable:
            raise
        except Exception as e:
            failure.append(e)
            log.warning("TWAIN acquisition ended: %s", type(e).__name__)
        finally:
            for t in timer:
                t.cancel()
        if not pages:
            name = type(failure[0]).__name__ if failure else ""
            if failure and name in ("SequenceError", "ExcDSBusy", "excDSBusy", "ScannerLocked"):
                raise ScannerUnavailable(BUSY_MESSAGE)
            raise ScannerUnavailable(EMPTY_MESSAGE)
        return pages

    def disconnect(self) -> None:
        for obj in (self._source, self._sm):
            try:
                if obj is not None:
                    obj.destroy()
            except Exception:
                pass
        _destroy_window(self._hwnd)
        self._source = self._sm = None
        self._hwnd = 0

    def _require_connected(self) -> None:
        if self._source is None:
            raise ScannerUnavailable("not connected to a TWAIN device")
