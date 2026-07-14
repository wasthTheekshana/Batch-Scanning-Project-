"""SFTP delivery channel — Phase 1 stub (HLD §12.2, §17.6).

Phase 3 replaces only the deliver() body with the paramiko
implementation; the class structure and registry entry stay unchanged.
No network code exists in Phase 1 — the stub logs and returns failure.
"""
from __future__ import annotations

import logging

from delivery.base import BaseDeliveryChannel, DeliveryResult

log = logging.getLogger(__name__)


class SFTPChannel(BaseDeliveryChannel):
    status_value = "delivered_sftp"

    def __init__(self, config: dict):
        self.config = config or {}

    @property
    def name(self) -> str:
        return "sftp"

    def is_available(self) -> bool:
        return False  # Phase 3

    def deliver(self, doc_path: str, metadata: dict) -> DeliveryResult:
        log.info("SFTP not yet enabled (Phase 3) — skipping %s", doc_path)
        return DeliveryResult(success=False, channel=self.name,
                              detail={"reason": "SFTP not yet enabled"})
