"""Guard rails for config blobs stored centrally: size cap and a
credential scan so secrets (e.g. SFTP passwords) never reach the cloud DB."""
from __future__ import annotations

import json

MAX_CONFIG_BYTES = 256 * 1024
_CREDENTIAL_KEYS = {"password", "passwd", "secret", "token", "api_key",
                    "private_key"}


def find_credential_path(node, path: str = "") -> str | None:
    """Dotted path of the first credential-looking key, else None."""
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if str(key).lower() in _CREDENTIAL_KEYS:
                return here
            found = find_credential_path(value, here)
            if found:
                return found
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found = find_credential_path(value, f"{path}[{i}]")
            if found:
                return found
    return None


def check_config(config: dict) -> None:
    if len(json.dumps(config)) > MAX_CONFIG_BYTES:
        raise ValueError(f"config too large (limit {MAX_CONFIG_BYTES} bytes)")
    bad = find_credential_path(config)
    if bad:
        raise ValueError(
            f"config must not contain credentials (found {bad}); keep "
            "secrets on the PC")
