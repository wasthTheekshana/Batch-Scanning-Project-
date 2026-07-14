"""Multi-page TIFF export with LZW or CCITT Group 4 compression (HLD §13)."""
from __future__ import annotations

from pathlib import Path

from PIL import Image


def write_multipage_tiff(images: list[str | Path], out: str | Path,
                         compression: str = "tiff_lzw") -> Path:
    """compression: 'tiff_lzw' or 'group4' (G4 forces 1-bit pages)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    frames = []
    for p in images:
        im = Image.open(p)
        if compression == "group4":
            im = im.convert("1")
        frames.append(im)
    first, rest = frames[0], frames[1:]
    first.save(str(out), save_all=True, append_images=rest,
               compression=compression)
    for f in frames:
        f.close()
    return out
