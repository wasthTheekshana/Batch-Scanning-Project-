"""The back of the LAST sheet in the feeder: how do we get it?

On the Kodak S3060 two-sided scanning works for every sheet except the last one:
after its front the driver answers "paper empty" and never hands over the back.
This tries several ways of asking again. Load EXACTLY ONE sheet printed on both
sides (different on each side), switch the scanner on and run:

    python scripts/duplex_last_sheet.py

It scans the front like the application does, then tries, in order: asking again
at once, asking again after a pause, re-arming the scan job and asking again,
and reading what the scanner says about itself. The first way that returns a
different picture is reported as the one that works.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np

from scanning import wia_source as w

DPI = 200


def decode(data: bytes):
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)


def differ(a, b) -> float:
    sa, sb = cv2.resize(a, (80, 110)), cv2.resize(b, (80, 110))
    return float(np.mean(np.abs(sa.astype(int) - sb.astype(int))))


def grab(item, folder: Path, name: str):
    try:
        image = item.Transfer(w.WIA_FORMAT_PNG)
        path = folder / f"{name}.png"
        image.SaveFile(str(path))
        return decode(path.read_bytes()), None
    except Exception as e:
        code = w._hresult(e)
        return None, hex(code) if code else str(e)


def main() -> int:
    source = w.WIASource()
    devices = source.list_devices()
    if not devices:
        raise SystemExit("No scanner found: switch it on.")
    source.connect(devices[0])
    folder = Path(tempfile.mkdtemp(prefix="duplex_last_"))

    # exactly what the application sets up before it scans
    source._configure(DPI, "grayscale", True)
    item = source._device.Items(1)
    props = {str(p.PropertyID): p for p in item.Properties}
    w._set_props(item.Properties, {w.WIA_DPI_X: DPI, w.WIA_DPI_Y: DPI,
                                   w.WIA_DATA_TYPE: 2, w.WIA_BITS_PER_PIXEL: 8})
    for pid in (w.WIA_START_X, w.WIA_START_Y):
        props[pid].Value = 0
    props[w.WIA_EXTENT_X].Value = w._full_width(props[w.WIA_EXTENT_X], DPI)
    props[w.WIA_EXTENT_Y].Value = w._snap(props[w.WIA_EXTENT_Y], w.A4_INCHES[1] * DPI)
    dev_props = {str(p.PropertyID): p for p in source._device.Properties}

    front, err = grab(item, folder, "front")
    if front is None:
        raise SystemExit(f"No front side came ({err}): is exactly one sheet loaded?")
    print(f"front: {front.shape[1]}x{front.shape[0]}  dark {(front < 128).mean() * 100:.2f}%")
    status = lambda: f"status={dev_props['3087'].Value} select={dev_props['3088'].Value} pages={dev_props['3096'].Value}"
    print("device after the front:", status())

    winner = None

    def attempt(label, action=None):
        nonlocal winner
        if action:
            action()
        back, err = grab(item, folder, label[:1])
        if back is None:
            print(f"  {label}: nothing ({err})")
            return
        same = differ(front, back) <= 8
        print(f"  {label}: image {back.shape[1]}x{back.shape[0]} dark {(back < 128).mean() * 100:.2f}% -> "
              + ("the same side again" if same else "A DIFFERENT PICTURE: the back"))
        if not same and winner is None:
            winner = label

    attempt("a. ask again at once")
    attempt("b. ask again after a 2 s pause", lambda: time.sleep(2))
    attempt("c. re-arm the job (pages=1) and ask again",
            lambda: dev_props["3096"].__setattr__("Value", 1))

    def rearm_all():
        dev_props["3088"].Value = w.FEEDER | w.DUPLEX
        dev_props["3096"].Value = 1
    attempt("d. re-send feeder+duplex and ask again", rearm_all)

    def new_connection():
        source.disconnect()
        source.connect(devices[0])
        dev_props.clear()
        dev_props.update({str(p.PropertyID): p for p in source._device.Properties})
        source._configure(DPI, "grayscale", True)
        nonlocal item
        item = source._device.Items(1)
    attempt("e. reconnect, set two-sided again and ask", new_connection)

    print("\nRESULT:", f"the back came back with '{winner}'" if winner
          else "no way of asking returned the back of the last sheet")
    print("images in", folder)
    return 0


if __name__ == "__main__":
    sys.exit(main())
