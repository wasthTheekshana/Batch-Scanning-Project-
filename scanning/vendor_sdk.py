"""Shared vendor SDK loader (HLD §11.2, Phase 2).

Vendor scanner SDKs ship as native DLLs dropped into vendor_sdks/.
Each wrapper looks its DLL up here; when absent the source degrades to
"no devices" so TWAIN/WIA remain the fallback (HLD §20 risk table).
"""
from __future__ import annotations

import ctypes
import logging
from pathlib import Path

log = logging.getLogger(__name__)

VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor_sdks"


def load_vendor_dll(dll_name: str, dll_path: str | Path | None = None):
    """Return a ctypes handle to the vendor DLL, or None when missing."""
    candidates = [Path(dll_path)] if dll_path else [
        VENDOR_DIR / dll_name, Path(dll_name)]
    for candidate in candidates:
        try:
            if candidate.exists():
                return ctypes.WinDLL(str(candidate))
        except OSError as e:
            log.warning("failed to load %s: %s", candidate, e)
    log.info("vendor DLL not present: %s (source disabled)", dll_name)
    return None
