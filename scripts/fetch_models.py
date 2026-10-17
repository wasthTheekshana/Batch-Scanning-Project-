"""Download the handwriting model (TrOCR small, handwritten; MIT licence) into models/trocr/.

    python scripts/fetch_models.py

About 250 MB. The files are not kept in git; the installer copies models/trocr next to the
application, and without them the application reads handwriting the older, weaker way.
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

BASE = "https://huggingface.co/Xenova/trocr-small-handwritten/resolve/main/"
FILES = {"onnx/encoder_model.onnx": "encoder_model.onnx",
         "onnx/decoder_model.onnx": "decoder_model.onnx",
         "tokenizer.json": "tokenizer.json", "config.json": "config.json"}
OUT = Path(__file__).resolve().parents[1] / "models" / "trocr"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for remote, local in FILES.items():
        target = OUT / local
        if target.exists() and target.stat().st_size > 0:
            print(f"have {local}")
            continue
        print(f"fetching {local} ...")
        urllib.request.urlretrieve(BASE + remote, target)
    print("models are in", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
