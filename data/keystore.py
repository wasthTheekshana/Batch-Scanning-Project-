"""Encryption key store backed by Windows DPAPI (HLD §10.2).

The 32-byte master key is generated once and stored protected with
CryptProtectData — tied to this machine + user, never visible in config
files. On platforms without pywin32 (CI) a plaintext fallback file is
used with a loud warning; production Windows installs always have DPAPI.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

KEY_BYTES = 32
_DPAPI_DESCRIPTION = "DOKScanner master key"


def _dpapi():
    try:
        import win32crypt
        return win32crypt
    except ImportError:
        return None


class KeyStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def get_or_create(self) -> bytes:
        if self.path.exists():
            return self._load()
        key = os.urandom(KEY_BYTES)
        self._store(key)
        return key

    # -- internals ------------------------------------------------------------
    def _store(self, key: bytes) -> None:
        dp = _dpapi()
        if dp is not None:
            blob = dp.CryptProtectData(key, _DPAPI_DESCRIPTION,
                                       None, None, None, 0)
            self.path.write_bytes(b"DPAPI1\0" + blob)
        else:
            log.warning("pywin32 missing — storing key WITHOUT DPAPI "
                        "protection (development fallback only)")
            self.path.write_bytes(b"PLAIN1\0" + key)

    def _load(self) -> bytes:
        data = self.path.read_bytes()
        if data.startswith(b"DPAPI1\0"):
            dp = _dpapi()
            if dp is None:
                raise RuntimeError(
                    "key was DPAPI-protected but pywin32 is unavailable")
            _desc, key = dp.CryptUnprotectData(data[7:], None, None, None, 0)
            return key
        if data.startswith(b"PLAIN1\0"):
            return data[7:]
        raise RuntimeError(f"unrecognised keystore format: {self.path}")

    def is_protected(self) -> bool:
        return self.path.exists() and \
            self.path.read_bytes().startswith(b"DPAPI1\0")
