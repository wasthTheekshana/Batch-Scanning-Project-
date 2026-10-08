"""Credential primitives. Passwords: bcrypt. API keys / enrollment codes are
high-entropy random values, so a plain SHA-256 at rest is sufficient and
allows an indexed lookup."""
from __future__ import annotations

import base64
import hashlib
import secrets

import bcrypt


def _prehash(password: str) -> bytes:
    """bcrypt 5 raises on inputs over 72 bytes (and older versions silently
    truncate). Pre-hashing to a fixed 44-byte value makes any password
    length safe and keeps every character significant."""
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prehash(password), hashed.encode())
    except ValueError:
        return False


_DUMMY_HASH = hash_password("timing-equaliser")


def burn_password_check(password: str) -> bool:
    """Spend the same time as a real check so an unknown username is not
    distinguishable from a wrong password by response time."""
    bcrypt.checkpw(_prehash(password), _DUMMY_HASH.encode())
    return False


def new_api_key() -> str:
    return "dsk_" + secrets.token_urlsafe(32)


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_enrollment_code() -> str:
    return secrets.token_urlsafe(12)


def sanitize_log_value(value: str | None, limit: int = 100) -> str:
    """Make client-supplied text safe to store in an audit entry: control
    characters (log forging) become '?', length is capped."""
    if not value:
        return ""
    cleaned = "".join("?" if ord(c) < 32 or ord(c) == 127 else c
                      for c in value)
    return cleaned[:limit]
