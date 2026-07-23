"""Base agent interface for portfolio allocation."""

from __future__ import annotations

import abc
from typing import Any


class BaseAgent(abc.ABC):
    """All portfolio agents implement this interface."""

    def reset(self, context: dict[str, Any] | None = None) -> None:
        """Optional hook called before a new evaluation run."""
        pass

    def observe(self, event: Any) -> None:
        """Optional hook called for market and news events."""
        pass

    @abc.abstractmethod
    def decide(self, observation: dict[str, Any]) -> dict[str, float]:
        """Return target portfolio weights keyed by asset id or ticker.

        Cash is implicit: cash_weight = 1 - sum(target_weights).
        """
        ...
