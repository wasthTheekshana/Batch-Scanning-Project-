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


def is_safe_file_name(name) -> bool:
    """True for a plain file name (or name template): non-empty, one path
    segment, no control characters, no '..', no trailing '.' or space and not
    a Windows reserved device name. ':' is allowed here because templates use
    it as a format spec ({counter:04d}); rendered names have it replaced."""
    if not isinstance(name, str) or not name:
        return False
    if "/" in name or "\\" in name or ".." in name:
        return False
    if any(ord(c) < 32 for c in name):
        return False
    if name.endswith((".", " ")):
        return False
    return name.split(".", 1)[0].rstrip().lower() not in _RESERVED


def contained_file(out_dir: Path, file_name: str) -> Path:
    """out_dir / file_name, or ValueError unless it is a plain file name that
    resolves to a direct child of out_dir."""
    out_dir = Path(out_dir)
    stem = file_name.split(".", 1)[0].rstrip().lower()
    if (not file_name or ":" in file_name or "/" in file_name
            or "\\" in file_name or stem in _RESERVED
            or any(ord(c) < 32 for c in file_name)):
        raise ValueError(f"unsafe output file name: {file_name!r}")
    target = out_dir / file_name
    if target.resolve().parent != out_dir.resolve():
        raise ValueError(f"unsafe output file name: {file_name!r}")
    return target
