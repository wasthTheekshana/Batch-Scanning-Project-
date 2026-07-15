"""CD/DVD burn channel — Phase 3 implementation (HLD §12.2, §18).

deliver() stages the document into the burn queue (checksummed); an
operator-triggered burn() runs the configured burner CLI (ImgBurn,
cdrecord, or any command template), verifies every file against the
manifest, and appends to the disc log. Config block:

    {"enabled": true,
     "staging_dir": "C:/DOKScanner/burn_queue",
     "burner_cmd": "\"C:/Program Files/ImgBurn/ImgBurn.exe\" /MODE BUILD "
                   "/SRC \"{source_dir}\" /VOLUMELABEL \"{label}\" /START /CLOSE",
     "verify_dir": null,          # optional mounted-disc path for verify
     "disc_capacity_mb": 4400}
"""
from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path

from delivery.base import BaseDeliveryChannel, DeliveryResult
from delivery.burn_queue import BurnQueue

log = logging.getLogger(__name__)

BURN_TIMEOUT_S = 30 * 60


class CDDVDChannel(BaseDeliveryChannel):
    status_value = "burned_to_disc"

    def __init__(self, config: dict):
        self.config = config or {}
        self._queue: BurnQueue | None = None

    @property
    def name(self) -> str:
        return "cd_dvd"

    @property
    def queue(self) -> BurnQueue:
        if self._queue is None:
            self._queue = BurnQueue(
                self.config.get("staging_dir", "burn_queue"))
        return self._queue

    def is_available(self) -> bool:
        return bool(self.config.get("enabled")
                    and self.config.get("staging_dir")
                    and self.config.get("burner_cmd"))

    def deliver(self, doc_path: str, metadata: dict) -> DeliveryResult:
        """Stage into the burn queue. The document is only
        'burned_to_disc' after burn() succeeds — staging returns
        success=False with queued detail so delivery_status stays
        pending until the disc is written."""
        if not self.is_available():
            return DeliveryResult(success=False, channel=self.name,
                                  detail={"error": "CD/DVD not configured"})
        try:
            capacity = float(self.config.get("disc_capacity_mb", 4400))
            doc_mb = Path(doc_path).stat().st_size / (1024 * 1024)
            if self.queue.total_size_mb() + doc_mb > capacity:
                return DeliveryResult(
                    success=False, channel=self.name,
                    detail={"error": "burn queue exceeds disc capacity",
                            "queued_mb": round(self.queue.total_size_mb(), 1),
                            "capacity_mb": capacity})
            entry = self.queue.add(doc_path, metadata)
            return DeliveryResult(
                success=False, channel=self.name,
                destination=str(self.queue.staging_dir / entry["file"]),
                checksum=entry["md5"],
                detail={"queued": True,
                        "queue_files": self.queue.file_count(),
                        "queue_mb": round(self.queue.total_size_mb(), 1)})
        except Exception as e:
            log.exception("burn staging failed")
            return DeliveryResult(success=False, channel=self.name,
                                  detail={"error": str(e)})

    # -- burning ------------------------------------------------------------------
    def burn(self, label: str | None = None) -> DeliveryResult:
        """Run the burner CLI on the staged queue, verify checksums,
        log the disc. Called from the Admin/operator UI or CLI."""
        if not self.is_available():
            return DeliveryResult(success=False, channel=self.name,
                                  detail={"error": "CD/DVD not configured"})
        if self.queue.file_count() == 0:
            return DeliveryResult(success=False, channel=self.name,
                                  detail={"error": "burn queue is empty"})
        label = label or datetime.now().strftime("DOK_%Y%m%d_%H%M")
        cmd = self.config["burner_cmd"].format(
            source_dir=str(self.queue.staging_dir), label=label)
        try:
            # Windows CreateProcess parses the quoted command string
            # itself — shlex would mangle backslash paths.
            proc = subprocess.run(cmd, capture_output=True,
                                  text=True, timeout=BURN_TIMEOUT_S)
            if proc.returncode != 0:
                return DeliveryResult(
                    success=False, channel=self.name,
                    detail={"error": f"burner exited {proc.returncode}",
                            "stderr": proc.stderr[-500:]})
        except (OSError, subprocess.SubprocessError) as e:
            return DeliveryResult(success=False, channel=self.name,
                                  detail={"error": str(e)})

        verify_dir = self.config.get("verify_dir")
        failures: list[str] = []
        verified = False
        if verify_dir:
            failures = self.queue.verify_against(verify_dir)
            verified = not failures
        result_detail = {
            "label": label, "files": self.queue.file_count(),
            "size_mb": round(self.queue.total_size_mb(), 1),
            "verified": verified, "verify_failures": failures,
        }
        self._append_disc_log(result_detail)
        success = not failures
        if success:
            self.queue.clear()
        return DeliveryResult(success=success, channel=self.name,
                              destination=label, detail=result_detail)

    def _append_disc_log(self, entry: dict) -> None:
        log_path = self.queue.staging_dir.parent / "burn_log.json"
        history = []
        if log_path.exists():
            history = json.loads(log_path.read_text(encoding="utf-8"))
        history.append({"time": datetime.now().isoformat(), **entry})
        log_path.write_text(json.dumps(history, indent=2), encoding="utf-8")
