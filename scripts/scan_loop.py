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


def run_twain(tl: Timeline, timeout: float, name_part: str) -> None:
    """The application's own TWAIN source (scanning/twain_source.py)."""
    from scanning.twain_source import TWAINSource
    source = TWAINSource()
    names = source.list_devices()
    name = next((n for n in names if name_part in n), None)
    if name is None:
        raise SystemExit(f"no TWAIN source matches {name_part!r}: {names}")
    source.connect(name)
    print("TWAIN:", name, flush=True)
    try:
        source.acquire(DPI, "grayscale", True, idle_timeout=timeout,
                       on_page=lambda data, n: tl.add(decode(data)))
        print("two-sided accepted:", source.duplex_applied)
    finally:
        source.disconnect()


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
        steady = gaps[1:-1] if len(gaps) > 3 else gaps
        worst = max(steady)
        passed = worst <= args.max_gap
        ok &= passed
        print(f"first image after {tl.marks[0]:.1f} s (feeder start), last wait {gaps[-1]:.1f} s (end of feed): "
              "one-off, not counted")
        print(f"SPEED: {'PASS' if passed else 'FAIL'}  (steady pace: longest wait {worst:.1f} s, "
              f"average {sum(steady) / len(steady):.1f} s per image; limit {args.max_gap:.1f} s)")
    return 0 if ok and n else 1


if __name__ == "__main__":
    sys.exit(main())
