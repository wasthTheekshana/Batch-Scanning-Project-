"""Encryption at rest for the central service (design: docs/superpowers/specs/2026-10-15-central-document-sync-design.md, 7.3).

One master key, held OUTSIDE the database (the environment, or a file it names), from which two independent
keys are derived:

  * an encryption key (AES-256-GCM) for secrets and index data stored in the database, so a copy of the
    database alone reveals no NIC, account number or webhook address;
  * a search key for the *blind index*: an HMAC of the normalised value, which lets the server find a document
    by an identifier (NIC, account number, customer id) without storing the identifier in clear.

Stored text carries a key version ("1:<token>"), so a key can be rotated: new data is written with the current
version and old data stays readable while the old key is still supplied (`CENTRAL_DATA_KEY_OLD`).
Losing the master key makes the encrypted data unreadable: back it up separately from the database.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import unicodedata
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

KEY_ENV = "CENTRAL_DATA_KEY"
KEY_FILE_ENV = "CENTRAL_DATA_KEY_FILE"
OLD_KEY_ENV = "CENTRAL_DATA_KEY_OLD"          # "<version>:<key>" of one earlier key, decrypt only
VERSION_ENV = "CENTRAL_DATA_KEY_VERSION"
NONCE_BYTES = 12


class KeyUnavailable(Exception):
    """No data key is configured, so nothing can be encrypted or decrypted."""


class DecryptionError(Exception):
    """The stored text was tampered with, is not ours, or was written with a key we do not have."""


def generate_key() -> str:
    """A new master key: 32 random bytes, URL-safe base64 (what CENTRAL_DATA_KEY holds)."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


def _master(text: str) -> bytes:
    try:
        raw = base64.urlsafe_b64decode(text.strip() + "=" * (-len(text.strip()) % 4))
    except ValueError as e:
        raise KeyUnavailable("the data key is not valid base64") from e
    if len(raw) != 32:
        raise KeyUnavailable("the data key must be 32 bytes (generate one with: python -m central.manage generate-key)")
    return raw


def _derive(master: bytes, purpose: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"dokscanner-central/" + purpose).derive(master)


@dataclass(frozen=True)
class _Version:
    number: int
    encrypt: bytes
    search: bytes


class DataKey:
    """The master key's derived keys, by version. `current` writes; every version reads."""

    def __init__(self, master: str, version: int = 1, old: dict[int, str] | None = None):
        self._versions: dict[int, _Version] = {}
        for number, text in {**(old or {}), version: master}.items():
            raw = _master(text)
            self._versions[number] = _Version(number, _derive(raw, b"encrypt"), _derive(raw, b"search"))
        self.current = version

    # -- encryption ------------------------------------------------------------------------------
    def encrypt(self, plaintext: bytes | str, context: str = "") -> str:
        """"<version>:<token>". `context` (a document id, a setting name) is authenticated with the
        data: text moved to another row, or to another purpose, fails to decrypt."""
        data = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
        nonce = secrets.token_bytes(NONCE_BYTES)
        sealed = AESGCM(self._versions[self.current].encrypt).encrypt(nonce, data, context.encode("utf-8"))
        return f"{self.current}:" + base64.urlsafe_b64encode(nonce + sealed).decode("ascii")

    def decrypt(self, stored: str, context: str = "") -> bytes:
        try:
            number, _, token = stored.partition(":")
            version = self._versions[int(number)]
            raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
            return AESGCM(version.encrypt).decrypt(raw[:NONCE_BYTES], raw[NONCE_BYTES:], context.encode("utf-8"))
        except (KeyError, ValueError, InvalidTag) as e:
            raise DecryptionError("cannot decrypt (wrong key, wrong place, or altered)") from e

    def decrypt_text(self, stored: str, context: str = "") -> str:
        return self.decrypt(stored, context).decode("utf-8")

    # -- blind index -------------------------------------------------------------------------------
    @staticmethod
    def normalise(value: str) -> str:
        """What two spellings of the same identifier have in common: upper-case letters and digits only
        ("553 216-789v" and "553216789V" are the same NIC)."""
        return re.sub(r"[^0-9A-Z]", "", unicodedata.normalize("NFKC", str(value)).upper())

    def blind(self, field: str, value: str, version: int | None = None) -> str:
        """Hex HMAC of the normalised value, scoped to the field so equal values in different fields differ.
        Empty after normalising -> "" (nothing to look up)."""
        norm = self.normalise(value)
        if not norm:
            return ""
        key = self._versions[version or self.current].search
        return hmac.new(key, f"{field}\x00{norm}".encode("utf-8"), hashlib.sha256).hexdigest()

    def blind_all(self, field: str, value: str) -> list[str]:
        """The blind hash under every key version we hold (a lookup during or after a key rotation)."""
        return [h for h in (self.blind(field, value, v) for v in self._versions) if h]


def load_data_key(environ=os.environ) -> DataKey | None:
    """The data key from the environment (CENTRAL_DATA_KEY, or the file CENTRAL_DATA_KEY_FILE names), or
    None when none is configured (features that store secrets or index data then refuse to run)."""
    text = environ.get(KEY_ENV, "").strip()
    path = environ.get(KEY_FILE_ENV, "").strip()
    if not text and path:
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read().strip()
        except OSError as e:
            raise KeyUnavailable(f"cannot read the data key file: {e.strerror}") from e
    if not text:
        return None
    old: dict[int, str] = {}
    previous = environ.get(OLD_KEY_ENV, "").strip()
    if previous:
        number, _, key = previous.partition(":")
        old[int(number)] = key
    return DataKey(text, int(environ.get(VERSION_ENV, "1")), old)
