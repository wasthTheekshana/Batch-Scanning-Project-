"""A repeatable scanner test: are both sides delivered, and how long is the wait between pages?

Load N sheets printed on BOTH sides (something different on each side), switch
the scanner on, then run ONE of:

    python scripts/scan_loop.py --backend wia   --sheets N
    python scripts/scan_loop.py --backend twain --sheets N

It scans the feeder two-sided at 200 dpi grayscale and prints, for every image,
when it arrived. Verdict:

    BACKS      PASS when 2*N images arrived (both sides of every sheet)
    SPEED      PASS when no image took longer than --max-gap seconds after the
               previous one (default 3.0: a feeder runs continuously)

The pictures are kept in a temp folder that is printed; nothing goes into the
application's data. A TWAIN scan that gets no answer is stopped cleanly after
--timeout seconds (the driver is closed properly, so the scanner is not left locked).
"""
from __future__ import annotations

import argparse
import ctypes
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np

DPI = 200


def decode(data: bytes):
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)


class Timeline:
    def __init__(self):
        self.start = time.monotonic()
        self.marks: list[float] = []
        self.images: list = []

    def add(self, img):
        self.marks.append(time.monotonic() - self.start)
        self.images.append(img)
        gap = self.marks[-1] - (self.marks[-2] if len(self.marks) > 1 else 0.0)
        print(f"  image {len(self.images):2d} at {self.marks[-1]:6.1f} s  (+{gap:4.1f} s)  "
              f"{img.shape[1]}x{img.shape[0]}  dark {(img < 128).mean() * 100:5.2f}%", flush=True)

    def gaps(self) -> list[float]:
        return [b - a for a, b in zip([0.0] + self.marks, self.marks)][1:]


def run_wia(tl: Timeline, limit: int = 500) -> None:
    from scanning.wia_source import WIASource
    source = WIASource()
    devices = source.list_devices()
    if not devices:
        raise SystemExit("No WIA scanner found: switch it on.")
    source.connect(devices[0])
    print("WIA:", devices[0], flush=True)
    source.acquire(DPI, "grayscale", True, on_page=lambda data, n: tl.add(decode(data)), max_pages=limit)
    print("two-sided accepted:", source.duplex_applied)
    source.disconnect()


def hidden_window() -> int:
    """A window TWAIN can post its messages to (it has none otherwise: the
    driver's 'image ready' signal never arrives and the scan waits forever)."""
    import win32con
    import win32gui
    return win32gui.CreateWindow("STATIC", "dok-twain", 0, 0, 0, 0, 0, 0, 0, 0, None)


def run_twain(tl: Timeline, timeout: float, name_part: str) -> None:
    import twain
    hwnd = hidden_window()
    sm = twain.SourceManager(hwnd)
    names = list(sm.GetSourceList())
    name = next((n for n in names if name_part in n), None)
    if name is None:
        raise SystemExit(f"no TWAIN source matches {name_part!r}: {names}")
    src = sm.OpenSource(name)
    print("TWAIN:", name, flush=True)
    for label, code, kind, value in (
            ("200 dpi", twain.ICAP_XRESOLUTION, twain.TWTY_FIX32, float(DPI)),
            ("200 dpi (y)", twain.ICAP_YRESOLUTION, twain.TWTY_FIX32, float(DPI)),
            ("grayscale", twain.ICAP_PIXELTYPE, twain.TWTY_UINT16, twain.TWPT_GRAY),
            ("feeder", twain.CAP_FEEDERENABLED, twain.TWTY_BOOL, True),
            ("two-sided", twain.CAP_DUPLEXENABLED, twain.TWTY_BOOL, True),
            ("all pages", twain.CAP_XFERCOUNT, twain.TWTY_INT16, -1)):
        try:
            src.SetCapability(code, kind, value)
        except Exception as e:
            print(f"  could not set {label}: {type(e).__name__}", flush=True)

    folder = Path(tempfile.mkdtemp(prefix="scan_loop_"))
    main_thread = ctypes.windll.kernel32.GetCurrentThreadId()

    def stop_cleanly():               # WM_QUIT ends the driver's wait; the driver is then closed properly
        print(f"  no answer from the driver in {timeout:.0f} s: stopping cleanly", flush=True)
        ctypes.windll.user32.PostThreadMessageW(main_thread, 0x0012, 0, 0)

    def after(image, remaining):
        path = folder / f"p{len(tl.images) + 1:02d}.bmp"
        image.save(str(path))
        tl.add(cv2.imread(str(path), cv2.IMREAD_GRAYSCALE))

    timer = threading.Timer(timeout, stop_cleanly)
    timer.daemon = True
    timer.start()
    try:
        src.acquire_natively(after, show_ui=False)
    except Exception as e:
        print("  acquisition ended:", type(e).__name__, e, flush=True)
    finally:
        timer.cancel()
        for obj in (src, sm):
            try:
                obj.destroy()
            except Exception:
                pass
        try:
            import win32gui
            win32gui.DestroyWindow(hwnd)
        except Exception:
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--backend", choices=("wia", "twain"), required=True)
    ap.add_argument("--sheets", type=int, default=0, help="how many two-sided sheets are loaded")
    ap.add_argument("--max-gap", type=float, default=3.0)
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--source", default="S2000f/S3000", help="part of the TWAIN source name")
    args = ap.parse_args(argv)

    tl = Timeline()
    try:
        if args.backend == "wia":
            run_wia(tl, 2 * args.sheets if args.sheets else 500)
        else:
            run_twain(tl, args.timeout, args.source)
    except SystemExit:
        raise
    except Exception as e:
        print(f"scan stopped: {type(e).__name__}: {e}")
    n = len(tl.images)
    print(f"\n{n} image(s) in {tl.marks[-1] if tl.marks else 0:.1f} s")
    ok = True
    if args.sheets:
        passed = n == 2 * args.sheets
        ok &= passed
        print(f"BACKS: {'PASS' if passed else 'FAIL'}  ({n} images; both sides of {args.sheets} sheets = {2 * args.sheets})")
    gaps = tl.gaps()
    if gaps:
        worst = max(gaps)
        passed = worst <= args.max_gap
        ok &= passed
        print(f"SPEED: {'PASS' if passed else 'FAIL'}  (longest wait between images {worst:.1f} s, "
              f"average {sum(gaps) / len(gaps):.1f} s; limit {args.max_gap:.1f} s)")
    return 0 if ok and n else 1


if __name__ == "__main__":
    sys.exit(main())
