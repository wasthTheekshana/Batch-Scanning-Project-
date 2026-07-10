"""Rule action plugin interface.

Each rule-engine action type implements BaseRuleAction; new actions are
added by registering a subclass without touching the engine.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseRuleAction(ABC):
    """All rule actions implement this interface."""

    @property
    @abstractmethod
    def action_type(self) -> str:
        """The DSL 'type' value this action handles."""

    @abstractmethod
    def apply(self, document: dict, value: Any) -> None:
        """Mutate the document dict according to the action."""
