"""Remove the unpacked copies of the application that crashed or killed runs left in the temp
folder (PyInstaller one-file builds unpack to %TEMP%\\_MEIxxxxx and normally clean up on exit).

    python scripts/clean_temp.py            # delete the stale ones
    python scripts/clean_temp.py --dry-run  # only list them

A folder is left alone when it was changed within the last MIN_AGE_HOURS (it may belong to a
running instance) or when a file in it cannot be removed (still in use). Nothing else in the
temp folder is touched.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

MIN_AGE_HOURS = 24


def size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def stale_folders(root: Path, min_age_hours: float = MIN_AGE_HOURS) -> list[Path]:
    cutoff = time.time() - min_age_hours * 3600
    return sorted(p for p in root.glob("_MEI*") if p.is_dir() and p.stat().st_mtime < cutoff)


def clean(root: Path, dry_run: bool = False, min_age_hours: float = MIN_AGE_HOURS) -> tuple[int, int]:
    """(folders removed, bytes freed)."""
    removed = freed = 0
    for folder in stale_folders(root, min_age_hours):
        try:
            nbytes = size(folder)
        except OSError:
            continue
        if dry_run:
            print(f"would remove {folder.name}  {nbytes / 1e6:.0f} MB")
            removed, freed = removed + 1, freed + nbytes
            continue
        shutil.rmtree(folder, ignore_errors=True)
        if folder.exists():                       # something in it is still in use
            print(f"kept {folder.name}: in use")
            continue
        print(f"removed {folder.name}  {nbytes / 1e6:.0f} MB")
        removed, freed = removed + 1, freed + nbytes
    return removed, freed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dir", default=os.environ.get("TEMP") or tempfile.gettempdir())
    args = ap.parse_args(argv)
    removed, freed = clean(Path(args.dir), args.dry_run)
    print(f"{'would free' if args.dry_run else 'freed'} {freed / 1e9:.2f} GB in {removed} folder(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
