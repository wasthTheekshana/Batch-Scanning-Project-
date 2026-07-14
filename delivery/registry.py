"""Delivery channel registry (HLD §12).

All channels are always registered — including Phase 3 stubs — so the
DeliveryWorker calls them uniformly; only enabled + available channels
actually deliver.
"""
from __future__ import annotations

from delivery.base import BaseDeliveryChannel
from delivery.cddvd_channel import CDDVDChannel
from delivery.local_folder import LocalFolderChannel
from delivery.sftp_channel import SFTPChannel


def build_channels(delivery_cfg: dict) -> list[BaseDeliveryChannel]:
    """Instantiate every channel from a project's delivery config."""
    cfg = delivery_cfg or {}
    return [
        LocalFolderChannel(cfg.get("local_folder", {})),
        SFTPChannel(cfg.get("sftp", {})),
        CDDVDChannel(cfg.get("cd_dvd", {})),
    ]


def active_channels(delivery_cfg: dict) -> list[BaseDeliveryChannel]:
    """Only the channels that are enabled in config AND available."""
    return [c for c in build_channels(delivery_cfg)
            if _enabled(delivery_cfg, c.name) and c.is_available()]


def _enabled(delivery_cfg: dict, name: str) -> bool:
    return bool((delivery_cfg or {}).get(name, {}).get("enabled"))
