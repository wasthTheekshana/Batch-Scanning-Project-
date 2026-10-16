"""What does this scanner actually do? Run it with the scanner switched on.

    python scripts/scanner_probe.py            # capabilities and duplex settings only (no paper used)
    python scripts/scanner_probe.py --scan 3   # then scan the 3 sheets in the feeder, both sides asked for

It reports whether the feeder is duplex, whether the scanner accepted the
two-sided setting, and (with --scan) how many images came back for the sheets
loaded: twice as many as sheets means two-sided scanning works. The images are
kept in a temporary folder that is printed, never in the application's data.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CAPS = {1: "feeder", 2: "flatbed", 4: "duplex", 8: "detect flatbed", 16: "detect scan",
        32: "detect feeder", 64: "detect duplex", 128: "feeder paper present",
        256: "duplex paper present", 512: "front first", 1024: "back first",
        2048: "front only", 4096: "back only", 8192: "next page", 16384: "prefeed",
        32768: "auto advance"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--scan", type=int, metavar="SHEETS",
                    help="also scan this many sheets from the feeder")
    ap.add_argument("--dpi", type=int, default=200)
    args = ap.parse_args(argv)

    from scanning.wia_source import (WIA_DPS_DOCUMENT_HANDLING_CAPABILITIES,
                                     WIASource)
    source = WIASource()
    devices = source.list_devices()
    if not devices:
        print("No scanner found: check that it is switched on and connected.")
        return 2
    print("scanner:", devices[0])
    source.connect(devices[0])
    caps = {str(p.PropertyID): p for p in source._device.Properties}.get(
        WIA_DPS_DOCUMENT_HANDLING_CAPABILITIES)
    value = int(caps.Value) if caps is not None else 0
    print(f"feeder capabilities {value}: " + ", ".join(n for b, n in CAPS.items() if value & b))
    print("duplex feeder:", source.get_capabilities().supports_duplex)

    source._configure(args.dpi, "grayscale", True)
    print("two-sided setting accepted by the scanner:", source.duplex_applied)
    if not args.scan:
        print("(no paper was used; add --scan N with N sheets in the feeder to test a scan)")
        return 0

    folder = Path(tempfile.mkdtemp(prefix="scanner_probe_"))
    pages = source.acquire(args.dpi, "grayscale", True)
    for i, data in enumerate(pages, 1):
        (folder / f"page_{i:03d}.png").write_bytes(data)
    print(f"{args.scan} sheet(s) loaded -> {len(pages)} image(s) returned (saved in {folder})")
    if source.duplex_applied and len(pages) == 2 * args.scan:
        print("RESULT: two-sided scanning works (one image per side).")
    elif len(pages) == args.scan:
        print("RESULT: one image per sheet: the scanner scanned the front only. "
              "Check the scanner's own two-sided setting or driver.")
    else:
        print("RESULT: unexpected count; look at the saved images.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
