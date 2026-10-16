"""Find out which setting stops the Kodak S3060 from handing over BOTH sides.

Load 5 or more sheets printed on BOTH sides (something different on each side),
switch the scanner on, then run:

    python scripts/duplex_experiment.py

Each attempt uses ONE sheet and asks for two images. They add the application's
own scan settings one step at a time, so the step after which the back side
disappears is the culprit. For every attempt it reports how many images arrived
and whether the second one is a different picture (the back) or nothing. The
pictures are kept in a temp folder that is printed; nothing goes into the
application's data.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np

from scanning import wia_source as w


def decode(data: bytes):
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)


def differ(a, b) -> float:
    """0 = the same picture, larger = different (compared at thumbnail size)."""
    sa, sb = cv2.resize(a, (80, 110)), cv2.resize(b, (80, 110))
    return float(np.mean(np.abs(sa.astype(int) - sb.astype(int))))


def step_dpi_gray(item, dpi):
    w._set_props(item.Properties, {w.WIA_DPI_X: dpi, w.WIA_DPI_Y: dpi,
                                   w.WIA_DATA_TYPE: 2, w.WIA_BITS_PER_PIXEL: 8})


def step_area(item, dpi):
    props = {str(p.PropertyID): p for p in item.Properties}
    for pid in (w.WIA_START_X, w.WIA_START_Y):
        props[pid].Value = 0
    props[w.WIA_EXTENT_X].Value = w._full_width(props[w.WIA_EXTENT_X], dpi)
    props[w.WIA_EXTENT_Y].Value = w._snap(props[w.WIA_EXTENT_Y], w.A4_INCHES[1] * dpi)


ATTEMPTS = [
    ("1. nothing set on the scan item", []),
    ("2. + resolution and grayscale", [step_dpi_gray]),
    ("3. + scan area (full width, A4 height)", [step_dpi_gray, step_area]),
]


def connect():
    source = w.WIASource()
    devices = source.list_devices()
    if not devices:
        raise SystemExit("No scanner found: switch it on.")
    source.connect(devices[0])
    return source


def attempt(label: str, steps, index: int, folder: Path, dpi: int = 200) -> int:
    source = connect()
    source._configure(dpi, "grayscale", True)
    item = source._device.Items(1)
    for step in steps:
        step(item, dpi)
    print(f"\n== {label}  (two-sided accepted: {source.duplex_applied})")
    images = []
    for n in range(2):
        try:
            image = item.Transfer(w.WIA_FORMAT_PNG)
            path = folder / f"a{index}_{n + 1}.png"
            image.SaveFile(str(path))
            img = decode(path.read_bytes())
            images.append(img)
            print(f"   image {n + 1}: {img.shape[1]}x{img.shape[0]}  dark {(img < 128).mean() * 100:5.2f}%")
        except Exception as e:
            code = w._hresult(e)
            print(f"   image {n + 1}: none ({hex(code) if code else e})")
            break
    source.disconnect()
    if len(images) == 2:
        both = differ(*images) > 8
        print("   ->", "BOTH SIDES (two different pictures)" if both else "the same picture twice")
        return 2 if both else 1
    return len(images)


def attempt_app(index: int, folder: Path, dpi: int = 200) -> int:
    """The application's own scan routine, stopped after two images."""
    source = connect()
    print(f"\n== {index}. the application's own acquire()")
    pages = source.acquire(dpi, "grayscale", True, max_pages=2)
    for n, data in enumerate(pages, 1):
        (folder / f"a{index}_{n}.png").write_bytes(data)
        img = decode(data)
        print(f"   image {n}: {img.shape[1]}x{img.shape[0]}  dark {(img < 128).mean() * 100:5.2f}%")
    source.disconnect()
    if len(pages) == 2:
        both = differ(decode(pages[0]), decode(pages[1])) > 8
        print("   ->", "BOTH SIDES" if both else "the same picture twice")
        return 2 if both else 1
    return len(pages)


def main() -> int:
    folder = Path(tempfile.mkdtemp(prefix="duplex_exp_"))
    outcome = {}
    for i, (label, steps) in enumerate(ATTEMPTS, 1):
        try:
            outcome[label] = attempt(label, steps, i, folder)
        except Exception as e:
            print(f"   stopped: {type(e).__name__}: {e}")
            outcome[label] = 0
    try:
        outcome["4. the application's own acquire()"] = attempt_app(4, folder)
    except Exception as e:
        print(f"   stopped: {type(e).__name__}: {e}")
        outcome["4. the application's own acquire()"] = 0
    print("\nSummary (images obtained from ONE two-sided sheet)")
    for label, n in outcome.items():
        print(f"  {label}: {n}  {'<- both sides' if n == 2 else '<- FRONT ONLY' if n == 1 else '<- none'}")
    print(f"\nimages in {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
