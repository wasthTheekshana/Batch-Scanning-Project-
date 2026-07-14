"""CD/DVD burn channel — Phase 1 stub (HLD §12.2, §17.6).

Phase 3 replaces only the deliver() body with the ImgBurn/cdrecord CLI
implementation plus checksum verification and disc logging.
"""
from __future__ import annotations

import logging

from delivery.base import BaseDeliveryChannel, DeliveryResult

log = logging.getLogger(__name__)


class CDDVDChannel(BaseDeliveryChannel):
    status_value = "burned_to_disc"

    def __init__(self, config: dict):
        self.config = config or {}

    @property
    def name(self) -> str:
        return "cd_dvd"

    def is_available(self) -> bool:
        return False  # Phase 3

    def deliver(self, doc_path: str, metadata: dict) -> DeliveryResult:
        log.info("CD/DVD not yet enabled (Phase 3) — skipping %s", doc_path)
        return DeliveryResult(success=False, channel=self.name,
                              detail={"reason": "CD/DVD not yet enabled"})
