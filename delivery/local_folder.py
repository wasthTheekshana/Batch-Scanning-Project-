"""Local folder delivery — copy + MD5 verification (HLD §12.2)."""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from core.safe_paths import safe_join
from delivery.base import BaseDeliveryChannel, DeliveryResult


def _md5(path: Path) -> str:
    h = hashlib.md5(usedforsecurity=False)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class LocalFolderChannel(BaseDeliveryChannel):
    status_value = "delivered_local"

    def __init__(self, config: dict):
        self.config = config or {}

    @property
    def name(self) -> str:
        return "local_folder"

    def is_available(self) -> bool:
        return bool(self.config.get("enabled") and self.config.get("path"))

    def deliver(self, doc_path: str, metadata: dict) -> DeliveryResult:
        src = Path(doc_path)
        try:
            dest_dir = safe_join(Path(self.config["path"]),
                                 metadata.get("output_folder") or "")
        except ValueError as e:  # rule tried to leave the delivery root
            return DeliveryResult(success=False, channel=self.name,
                                  detail={"error": str(e)})
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name

        src_md5 = _md5(src)
        shutil.copy2(src, dest)
        dest_md5 = _md5(dest)
        ok = src_md5 == dest_md5
        return DeliveryResult(
            success=ok, channel=self.name, destination=str(dest),
            checksum=dest_md5,
            detail={} if ok else {"error": "checksum mismatch after copy"})
