"""Guards for rule-supplied output sub-folders.

A rule's set_folder value (which can arrive from central) is joined onto the
output / delivery root. pathlib replaces the base when the right side is
absolute, so every value must be proven to stay inside that root.
"""
from __future__ import annotations

from pathlib import Path

_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)})


def is_safe_relative_subpath(sub) -> bool:
    """True for "" (no sub-folder) or a relative path of plain segments."""
    if not isinstance(sub, str):
        return False
    if sub == "":
        return True
    if ":" in sub or any(ord(c) < 32 for c in sub):
        return False
    if sub[0] in "/\\":  # absolute, UNC, rooted
        return False
    for seg in sub.replace("\\", "/").split("/"):
        if seg in ("", ".", ".."):
            return False
        if seg.endswith((".", " ")):
            return False
        if seg.split(".", 1)[0].rstrip().lower() in _RESERVED:
            return False
    return True


def safe_join(base: Path, sub: str) -> Path:
    """base / sub, or ValueError if sub is unsafe or escapes base."""
    if not is_safe_relative_subpath(sub):
        raise ValueError(f"unsafe output folder: {sub!r}")
    base = Path(base)
    if not sub:
        return base
    joined = base / sub
    if not joined.resolve().is_relative_to(base.resolve()):
        raise ValueError(f"unsafe output folder: {sub!r}")
    return joined
