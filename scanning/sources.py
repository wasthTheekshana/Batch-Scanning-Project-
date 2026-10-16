"""Scanner source registry — name → BaseScannerSource class.

The scan wizard builds its source list from here; adding a Phase 2
vendor SDK is one registry entry (HLD §3 plugin-first principle).
"""
from __future__ import annotations

import re

from scanning.alaris_source import AlarisSource
from scanning.base import BaseScannerSource
from scanning.czur_source import CZURSource
from scanning.kodak_source import KodakSource
from scanning.twain_source import TWAINSource
from scanning.viisan_source import ViisanSource
from scanning.wia_source import WIASource

SOURCE_REGISTRY: dict[str, type[BaseScannerSource]] = {
    "twain": TWAINSource,
    "wia": WIASource,
    "kodak": KodakSource,
    "alaris": AlarisSource,
    "czur": CZURSource,
    "viisan": ViisanSource,
}


def available_sources() -> dict[str, list[str]]:
    """name → device list, for every source that reports devices."""
    found: dict[str, list[str]] = {}
    for name, cls in SOURCE_REGISTRY.items():
        try:
            devices = cls().list_devices()
        except Exception:
            devices = []
        if devices:
            found[name] = devices
    return found


def _models(name: str) -> list[str]:
    """Model codes in a device name: 'KODAK S3060 Scanner' -> ['S3060'],
    'KODAK Scanner: S2000f/S3000' -> ['S2000', 'S3000']."""
    return [m.upper() for m in re.findall(r"[A-Za-z]\d{3,4}", name)]


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def same_family(wia_name: str, twain_name: str) -> bool:
    """Whether a WIA device and a TWAIN source are the same scanner: they share
    a model family (S3060 and S3000 both begin S30), which a vendor's single
    driver covers ("S2000f/S3000" serves the S3000 series)."""
    return any(_common_prefix(a, b) >= 3
               for a in _models(wia_name) for b in _models(twain_name))


def preferred_choice(devices: dict[str, list[str]]) -> tuple[str, str] | None:
    """The (source, device) to pre-select. A scanner that is reachable through
    both WIA and its vendor's TWAIN driver is used through TWAIN: it feeds
    continuously and delivers both sides of every sheet, where WIA restarts a
    job per page (4 s each) and loses backs. None when no scanner has a TWAIN
    entry of its own (then nothing is pre-selected)."""
    for wia in devices.get("wia", []):
        for twain in devices.get("twain", []):
            if same_family(wia, twain):
                return ("twain", twain)
    return None
