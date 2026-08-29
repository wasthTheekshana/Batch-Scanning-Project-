"""Duplicate detection: MD5 exact match + pHash near-duplicate +
barcode cross-check (HLD §4 Layer 5).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import imagehash
from PIL import Image

PHASH_MAX_DISTANCE = 5


def fingerprint(img_path: str | Path) -> tuple[str, str]:
    """(md5_hex, phash_hex) of an image file."""
    data = Path(img_path).read_bytes()
    md5 = hashlib.md5(data, usedforsecurity=False).hexdigest()
    with Image.open(img_path) as im:
        phash = str(imagehash.phash(im))
    return md5, phash


def phash_distance(a: str, b: str) -> int:
    return imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b)


def find_duplicate(md5: str, phash: str,
                   known: list[dict], barcode: str | None = None) -> str | None:
    """Return the document_id of the first duplicate among `known`
    fingerprints, else None.

    known: [{'document_id': ..., 'md5': ..., 'phash': ..., 'barcode': ...}]
    Match order: exact MD5 → pHash Hamming ≤ 5 → same barcode value.
    """
    for k in known:
        if k.get("md5") == md5:
            return k["document_id"]
    for k in known:
        if k.get("phash") and phash_distance(phash, k["phash"]) <= PHASH_MAX_DISTANCE:
            return k["document_id"]
    if barcode:
        for k in known:
            if k.get("barcode") and k["barcode"] == barcode:
                return k["document_id"]
    return None
