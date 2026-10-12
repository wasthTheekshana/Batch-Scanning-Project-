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


def protect_bytes(secret: bytes, description: str) -> bytes:
    """DPAPI-protect `secret` (machine+user bound); plaintext fallback with a
    loud warning where pywin32 is missing (development only)."""
    dp = _dpapi()
    if dp is not None:
        blob = dp.CryptProtectData(secret, description, None, None, None, 0)
        return b"DPAPI1\0" + blob
    log.warning("pywin32 missing — storing key WITHOUT DPAPI "
                "protection (development fallback only)")
    return b"PLAIN1\0" + secret


def unprotect_bytes(data: bytes) -> bytes:
    if data.startswith(b"DPAPI1\0"):
        dp = _dpapi()
        if dp is None:
            raise RuntimeError(
                "key was DPAPI-protected but pywin32 is unavailable")
        _desc, secret = dp.CryptUnprotectData(data[7:], None, None, None, 0)
        return secret
    if data.startswith(b"PLAIN1\0"):
        return data[7:]
    raise RuntimeError("unrecognised keystore format")


class ProtectedBlob:
    """A small secret (e.g. the central API key) stored like the master key."""

    def __init__(self, path: str | Path,
                 description: str = "DOKScanner secret"):
        self.path = Path(path)
        self.description = description
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def exists(self) -> bool:
        return self.path.exists()

    def save(self, secret: bytes) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_bytes(protect_bytes(secret, self.description))
        os.replace(tmp, self.path)

    def load(self) -> bytes | None:
        if not self.path.exists():
            return None
        return unprotect_bytes(self.path.read_bytes())

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)


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
        self.path.write_bytes(protect_bytes(key, _DPAPI_DESCRIPTION))

    def _load(self) -> bytes:
        try:
            return unprotect_bytes(self.path.read_bytes())
        except RuntimeError as e:
            if "unrecognised keystore format" in str(e):
                raise RuntimeError(
                    f"unrecognised keystore format: {self.path}") from e
            raise

    def is_protected(self) -> bool:
        return self.path.exists() and \
            self.path.read_bytes().startswith(b"DPAPI1\0")
