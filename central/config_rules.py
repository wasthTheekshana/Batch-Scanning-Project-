"""Guard rails for config blobs stored centrally: size cap and a
credential scan so secrets (e.g. SFTP passwords) never reach the cloud DB."""
from __future__ import annotations

import json
import re

MAX_CONFIG_BYTES = 256 * 1024
MAX_NESTING_DEPTH = 64
_CREDENTIAL_KEY_PARTS = {"password", "passwd", "passphrase", "secret",
                         "token", "apikey", "privatekey"}
_ALLOWED_KEY_PATTERNS = {"keyfile", "key_file"}  # Paths, not secrets
_URL_USERINFO_PATTERN = re.compile(r"://[^/\s:@]+:[^/\s@]+@")
_PEM_PRIVATE_KEY_PATTERN = re.compile(r"-----BEGIN.*PRIVATE KEY")


def _normalize_key(key: str) -> str:
    """Normalize key by removing non-alphanumeric characters and lowercasing."""
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _is_credential_key(key: str) -> bool:
    """Check if a key looks like a credential (password, token, etc.)."""
    if _normalize_key(key) in _ALLOWED_KEY_PATTERNS:
        return False
    normalized = _normalize_key(key)
    for part in _CREDENTIAL_KEY_PARTS:
        if part in normalized:
            return True
    return False


def _is_credential_value(value: str) -> bool:
    """Check if a string value looks like a credential."""
    if not isinstance(value, str):
        return False
    # Check for URL userinfo (sftp://user:pass@host)
    if _URL_USERINFO_PATTERN.search(value):
        return True
    # Check for PEM private key headers
    if "-----BEGIN" in value and "PRIVATE KEY" in value:
        return True
    return False


def find_credential_path(node, path: str = "") -> str | None:
    """Dotted path of the first credential-looking key or value, else None.
    Uses iterative approach with depth limit to handle deep nesting."""
    stack = [(node, path, 0)]  # (node, path, depth)

    while stack:
        current, current_path, depth = stack.pop()

        if depth > MAX_NESTING_DEPTH:
            raise ValueError("config nested too deeply")

        if isinstance(current, dict):
            for key, value in current.items():
                here = f"{current_path}.{key}" if current_path else str(key)

                # Check if key is a credential
                if _is_credential_key(key):
                    return here

                # Check if value is a credential string
                if _is_credential_value(value):
                    return here

                # Continue traversal
                if isinstance(value, (dict, list)):
                    stack.append((value, here, depth + 1))

        elif isinstance(current, list):
            for i, value in enumerate(current):
                here = f"{current_path}[{i}]"

                # Check if value is a credential string
                if _is_credential_value(value):
                    return here

                # Continue traversal
                if isinstance(value, (dict, list)):
                    stack.append((value, here, depth + 1))

    return None


def check_config(config: dict) -> None:
    """Validate config: no credentials, under size limit, not too deeply nested."""
    # Check for credentials and nesting depth BEFORE json.dumps to avoid
    # json.dumps recursion limit issues
    bad = find_credential_path(config)
    if bad:
        raise ValueError(
            f"config must not contain credentials (found {bad}); keep "
            "secrets on the PC")

    if len(json.dumps(config)) > MAX_CONFIG_BYTES:
        raise ValueError(f"config too large (limit {MAX_CONFIG_BYTES} bytes)")
