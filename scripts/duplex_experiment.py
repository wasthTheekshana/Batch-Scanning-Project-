"""Find out which setting stops the Kodak S3060 from handing over BOTH sides.

Load 8 or more sheets printed on BOTH sides (something different on each side),
switch the scanner on, then run:

    python scripts/duplex_experiment.py

Each attempt uses ONE sheet and asks for two images, with a different mix of the
application's scan settings (and in a different order), so the attempts that
return only the front reveal which setting loses the back side. It reports for
every attempt how many images arrived and whether the second is a different
picture (the back). The pictures are kept in a temp folder that is printed;
nothing goes into the application's data.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np

from scanning import wia_source as w

DPI = 200

# (label, two-sided first?, data type, bits, set the dpi?, set the scan area?)
ATTEMPTS = [
    ("A. nothing on the item (baseline)",                   True,  None, None, False, False),
    ("B. grayscale only",                                   True,  2,    8,    False, False),
    ("C. resolution only",                                  True,  None, None, True,  False),
    ("D. colour only",                                      True,  3,    24,   False, False),
    ("E. black and white set explicitly",                   True,  0,    1,    False, False),
    ("F. grayscale + resolution, two-sided asked LAST",     False, 2,    8,    True,  False),
    ("G. grayscale + resolution + area, two-sided LAST",    False, 2,    8,    True,  True),
    ("H. black and white + area, two-sided first",          True,  0,    1,    False, True),
]


def decode(data: bytes):
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)


def differ(a, b) -> float:
    """0 = the same picture, larger = different (compared at thumbnail size)."""
    sa, sb = cv2.resize(a, (80, 110)), cv2.resize(b, (80, 110))
    return float(np.mean(np.abs(sa.astype(int) - sb.astype(int))))


def connect():
    source = w.WIASource()
    devices = source.list_devices()
    if not devices:
        raise SystemExit("No scanner found: switch it on.")
    source.connect(devices[0])
    return source


def item_settings(item, dtype, bits, set_dpi, area):
    values = {}
    if set_dpi:
        values.update({w.WIA_DPI_X: DPI, w.WIA_DPI_Y: DPI})
    if dtype is not None:
        values.update({w.WIA_DATA_TYPE: dtype, w.WIA_BITS_PER_PIXEL: bits})
    if values:
        w._set_props(item.Properties, values)
    if area:
        props = {str(p.PropertyID): p for p in item.Properties}
        for pid in (w.WIA_START_X, w.WIA_START_Y):
            props[pid].Value = 0
        props[w.WIA_EXTENT_X].Value = w._full_width(props[w.WIA_EXTENT_X], DPI)
        props[w.WIA_EXTENT_Y].Value = w._snap(props[w.WIA_EXTENT_Y], w.A4_INCHES[1] * DPI)


def attempt(index, label, duplex_first, dtype, bits, set_dpi, area, folder):
    source = connect()
    item = source._device.Items(1)
    if duplex_first:
        source._configure(DPI, "grayscale", True)
        item = source._device.Items(1)
        item_settings(item, dtype, bits, set_dpi, area)
    else:
        item_settings(item, dtype, bits, set_dpi, area)
        source._configure(DPI, "grayscale", True)
        item = source._device.Items(1)
    print(f"\n== {label}  (two-sided accepted: {source.duplex_applied})")
    images = []
    for n in range(2):
        try:
            image = item.Transfer(w.WIA_FORMAT_PNG)
            path = folder / f"{label[0]}_{n + 1}.png"
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
        return 2 if differ(*images) > 8 else 1
    return len(images)


def main() -> int:
    folder = Path(tempfile.mkdtemp(prefix="duplex_exp_"))
    outcome = {}
    for i, spec in enumerate(ATTEMPTS, 1):
        label = spec[0]
        try:
            outcome[label] = attempt(i, *spec, folder)
        except SystemExit:
            raise
        except Exception as e:
            print(f"   stopped: {type(e).__name__}: {e}")
            outcome[label] = 0
        if outcome[label] == 0:
            print("   (no sheet came: the feeder is empty, stopping)")
            break
    print("\nSummary (images obtained from ONE two-sided sheet)")
    for label, n in outcome.items():
        print(f"  {label}: {n}  {'<- both sides' if n == 2 else '<- FRONT ONLY' if n == 1 else '<- none'}")
    print(f"\nimages in {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
