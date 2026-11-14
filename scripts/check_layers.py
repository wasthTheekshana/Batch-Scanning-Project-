"""Check the layering rule: dependencies point one way (see README, "Layering").

    python scripts/check_layers.py        # exits 1 and lists every import that goes the wrong way

Each package may import only the packages listed for it here. Imports inside functions count too.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# package -> the packages it may import (itself is always allowed)
ALLOWED: dict[str, set[str]] = {
    "shared": set(),
    "data": {"shared"},
    "imaging": {"shared"},
    "ocr": {"shared", "imaging"},
    "qc": {"shared", "imaging", "ocr"},
    "separation": {"shared", "ocr"},
    "scanning": {"shared", "imaging"},
    "export": {"shared", "ocr"},
    "delivery": {"shared"},
    "custody": {"shared", "data", "separation"},
    "projects": {"shared"},
    "security": {"shared", "data"},
    "search": {"shared", "data", "export"},
    "sync": {"shared", "data", "projects"},
    "pipeline": {"shared", "data", "projects", "imaging", "ocr", "qc", "separation", "scanning", "export",
                 "delivery", "custody"},
    "ui": {"shared", "data", "projects", "security", "search", "sync", "pipeline", "imaging", "ocr", "separation",
           "scanning", "export", "delivery", "custody", "qc"},
    "central": {"projects"},            # the service reuses the project schema and validator, nothing else
}
INTERNAL = set(ALLOWED) | {"vendor_sdks"}


def imports_of(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return set()
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found & INTERNAL


def violations() -> list[str]:
    out = []
    for package, allowed in ALLOWED.items():
        for path in sorted((ROOT / package).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for target in sorted(imports_of(path) - allowed - {package}):
                out.append(f"{path.relative_to(ROOT).as_posix()}: {package} must not import {target}")
    return out


def main() -> int:
    bad = violations()
    for line in bad:
        print(line)
    print(f"{len(bad)} layering violation(s)" if bad else "layering ok")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
