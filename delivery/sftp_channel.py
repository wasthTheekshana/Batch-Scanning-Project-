"""SFTP delivery channel — Phase 3 implementation (HLD §12.2, §18).

LAN SFTP only. Config block (project delivery.sftp):

    {"enabled": true, "host": "192.168.1.10", "port": 22,
     "username": "scans", "password": "..." | "key_file": "C:/keys/id_rsa",
     "remote_path": "/incoming", "timeout": 10}

deliver() uploads with 3 retry attempts, verifies the remote file size
matches, and records the local MD5 checksum. Never raises — every
failure returns DeliveryResult(success=False, detail={'error': ...}).
"""
from __future__ import annotations

import hashlib
import logging
import posixpath
import time
from pathlib import Path

from shared.safe_paths import is_safe_relative_subpath
from delivery.base import BaseDeliveryChannel, DeliveryResult

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAY_S = 1.0


def _paramiko():
    try:
        import paramiko
        return paramiko
    except ImportError:
        return None


def _md5(path: Path) -> str:
    h = hashlib.md5(usedforsecurity=False)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class SFTPChannel(BaseDeliveryChannel):
    status_value = "delivered_sftp"

    def __init__(self, config: dict):
        self.config = config or {}
        self.max_attempts = int(self.config.get("max_attempts", MAX_ATTEMPTS))
        self.retry_delay = float(self.config.get("retry_delay", RETRY_DELAY_S))

    @property
    def name(self) -> str:
        return "sftp"

    def is_available(self) -> bool:
        return bool(self.config.get("enabled")
                    and self.config.get("host")
                    and self.config.get("username")
                    and _paramiko() is not None)

    def deliver(self, doc_path: str, metadata: dict) -> DeliveryResult:
        if not self.is_available():
            return DeliveryResult(success=False, channel=self.name,
                                  detail={"error": "SFTP not configured"})
        if not is_safe_relative_subpath(
                (metadata.get("output_folder") or "")):
            return DeliveryResult(
                success=False, channel=self.name,
                detail={"error": "unsafe output folder: "
                                 f"{metadata.get('output_folder')!r}"})
        src = Path(doc_path)
        last_error = "unknown"
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self._upload(src, metadata)
            except Exception as e:
                last_error = str(e)
                log.warning("SFTP attempt %d/%d failed: %s",
                            attempt, self.max_attempts, e)
                if attempt < self.max_attempts:
                    time.sleep(self.retry_delay)
        return DeliveryResult(success=False, channel=self.name,
                              detail={"error": last_error,
                                      "attempts": self.max_attempts})

    # -- internals -----------------------------------------------------------
    def _connect(self):
        pk = _paramiko()
        transport = pk.Transport((self.config["host"],
                                  int(self.config.get("port", 22))))
        if self.config.get("key_file"):
            key = pk.RSAKey.from_private_key_file(self.config["key_file"])
            transport.connect(username=self.config["username"], pkey=key)
        else:
            transport.connect(username=self.config["username"],
                              password=self.config.get("password", ""))
        return transport, pk.SFTPClient.from_transport(transport)

    def _upload(self, src: Path, metadata: dict) -> DeliveryResult:
        transport, sftp = self._connect()
        try:
            remote_dir = self.config.get("remote_path", ".")
            if sub := metadata.get("output_folder"):
                remote_dir = posixpath.join(remote_dir,
                                            sub.replace("\\", "/"))
            self._mkdirs(sftp, remote_dir)
            remote_file = posixpath.join(remote_dir, src.name)
            local_size = src.stat().st_size
            sftp.put(str(src), remote_file)
            remote_size = sftp.stat(remote_file).st_size
            if remote_size != local_size:
                raise IOError(f"size mismatch after upload: "
                              f"local {local_size} != remote {remote_size}")
            return DeliveryResult(
                success=True, channel=self.name, destination=remote_file,
                checksum=_md5(src),
                detail={"host": self.config["host"], "size": local_size})
        finally:
            try:
                sftp.close()
                transport.close()
            except Exception:
                log.debug("closing the SFTP connection failed (ignored)", exc_info=True)

    @staticmethod
    def _mkdirs(sftp, remote_dir: str) -> None:
        parts = [p for p in remote_dir.replace("\\", "/").split("/") if p]
        path = "/" if remote_dir.startswith("/") else ""
        for part in parts:
            path = posixpath.join(path, part) if path else part
            try:
                sftp.stat(path)
            except FileNotFoundError:
                sftp.mkdir(path)
