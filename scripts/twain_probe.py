"""What does the Kodak do through TWAIN? (the WIA driver loses the back of the last sheet)

    python scripts/twain_probe.py              # opens the driver and reports its two-sided capabilities (no paper)
    python scripts/twain_probe.py --scan       # also scans what is in the feeder, two-sided, 200 dpi

Pictures from --scan are kept in a temp folder that is printed; nothing goes
into the application's data.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--scan", action="store_true", help="also scan the sheets in the feeder")
    ap.add_argument("--source", default="S2000f/S3000", help="part of the TWAIN source name")
    args = ap.parse_args(argv)

    import twain
    sm = twain.SourceManager(0)
    names = list(sm.GetSourceList())
    print("TWAIN sources:", names)
    name = next((n for n in names if args.source in n), None)
    if name is None:
        print("no source matches", args.source)
        return 2
    src = sm.OpenSource(name)
    print("opened:", name)

    def cap(label, code):
        try:
            value = src.GetCapability(code)
            print(f"  {label}: {value[1][:2] if isinstance(value[1], tuple) else value[1]}")
        except Exception as e:
            print(f"  {label}: unavailable ({type(e).__name__})")
    cap("duplex supported (CAP_DUPLEX)", twain.CAP_DUPLEX)
    cap("duplex enabled (CAP_DUPLEXENABLED)", twain.CAP_DUPLEXENABLED)
    cap("feeder enabled (CAP_FEEDERENABLED)", twain.CAP_FEEDERENABLED)
    cap("feeder loaded (CAP_FEEDERLOADED)", twain.CAP_FEEDERLOADED)

    if not args.scan:
        src.destroy()
        sm.destroy()
        print("(no paper used; add --scan to scan the feeder)")
        return 0

    for label, code, kind, value in (
            ("200 dpi", twain.ICAP_XRESOLUTION, twain.TWTY_FIX32, 200.0),
            ("200 dpi (y)", twain.ICAP_YRESOLUTION, twain.TWTY_FIX32, 200.0),
            ("grayscale", twain.ICAP_PIXELTYPE, twain.TWTY_UINT16, twain.TWPT_GRAY),
            ("feeder", twain.CAP_FEEDERENABLED, twain.TWTY_BOOL, True),
            ("autofeed", twain.CAP_AUTOFEED, twain.TWTY_BOOL, True),
            ("two-sided", twain.CAP_DUPLEXENABLED, twain.TWTY_BOOL, True)):
        try:
            src.SetCapability(code, kind, value)
            print(f"  set: {label}")
        except Exception as e:
            print(f"  could not set {label}: {type(e).__name__}: {e}")

    folder = Path(tempfile.mkdtemp(prefix="twain_probe_"))
    images = []

    def after(image, remaining):
        path = folder / f"p{len(images) + 1:02d}.bmp"
        image.save(str(path))
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        images.append(img)
        print(f"  image {len(images)}: {img.shape[1]}x{img.shape[0]}  dark {(img < 128).mean() * 100:5.2f}%"
              f"  (the driver says {remaining} more)")

    import os
    import threading

    def give_up():                       # never hang: say so and stop
        print("TIMED OUT: the driver did not answer in 60 seconds", flush=True)
        os._exit(4)
    watchdog = threading.Timer(60, give_up)
    watchdog.daemon = True
    watchdog.start()
    try:
        src.acquire_natively(after, show_ui=False)       # runs its own event loop; no driver window
    except Exception as e:
        print("  acquisition ended:", type(e).__name__, e)
    watchdog.cancel()
    print(f"\n{len(images)} image(s) -> {folder}")
    try:
        src.destroy()
        sm.destroy()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
