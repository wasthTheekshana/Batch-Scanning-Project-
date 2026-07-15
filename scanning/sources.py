"""Scanner source registry — name → BaseScannerSource class.

The scan wizard builds its source list from here; adding a Phase 2
vendor SDK is one registry entry (HLD §3 plugin-first principle).
"""
from __future__ import annotations

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
