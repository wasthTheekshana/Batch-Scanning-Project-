"""CD/DVD burn queue (HLD §18 Phase 3 weeks 9-10).

Documents accumulate in a staging session folder with a manifest of MD5
checksums; a burn writes the whole session to disc and verifies every
file against the manifest afterwards.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path


def _md5(path: Path) -> str:
    h = hashlib.md5(usedforsecurity=False)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class BurnQueue:
    MANIFEST = "manifest.json"

    def __init__(self, staging_dir: str | Path):
        self.staging_dir = Path(staging_dir)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self.staging_dir / self.MANIFEST

    def add(self, doc_path: str | Path, metadata: dict | None = None) -> dict:
        src = Path(doc_path)
        dest = self.staging_dir / src.name
        counter = 1
        while dest.exists():                    # never overwrite queued docs
            dest = self.staging_dir / f"{src.stem}_{counter}{src.suffix}"
            counter += 1
        shutil.copy2(src, dest)
        entry = {"file": dest.name, "md5": _md5(dest),
                 "size": dest.stat().st_size,
                 "queued_at": datetime.now().isoformat(),
                 "metadata": metadata or {}}
        manifest = self.manifest()
        manifest["files"].append(entry)
        self._manifest_path.write_text(json.dumps(manifest, indent=2),
                                       encoding="utf-8")
        return entry

    def manifest(self) -> dict:
        if self._manifest_path.exists():
            return json.loads(self._manifest_path.read_text(encoding="utf-8"))
        return {"files": []}

    def total_size_mb(self) -> float:
        return sum(e["size"] for e in self.manifest()["files"]) / (1024 * 1024)

    def file_count(self) -> int:
        return len(self.manifest()["files"])

    def verify_against(self, disc_dir: str | Path) -> list[str]:
        """Compare every manifest entry with the burned copy.
        Returns the list of failed file names (empty = all verified)."""
        disc = Path(disc_dir)
        failures = []
        for entry in self.manifest()["files"]:
            burned = disc / entry["file"]
            if not burned.exists() or _md5(burned) != entry["md5"]:
                failures.append(entry["file"])
        return failures

    def clear(self) -> None:
        for f in self.staging_dir.iterdir():
            if f.is_file():
                f.unlink()
