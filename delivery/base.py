"""Delivery channel plugin interface (HLD §12.1)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DeliveryResult:
    success: bool
    channel: str
    destination: str = ""
    checksum: Optional[str] = None
    detail: dict = field(default_factory=dict)


class BaseDeliveryChannel(ABC):
    """All delivery channels implement this interface."""

    #: value written to documents.delivery_status on success
    status_value: str = "pending"

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def is_available(self) -> bool:
        """True if the channel is configured and reachable."""

    @abstractmethod
    def deliver(self, doc_path: str, metadata: dict) -> DeliveryResult: ...
