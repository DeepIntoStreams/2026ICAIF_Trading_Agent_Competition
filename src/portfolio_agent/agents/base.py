"""Base agent interface for portfolio allocation."""

from __future__ import annotations

import abc
from typing import Any


class BaseAgent(abc.ABC):
    """All portfolio agents implement this interface."""

    @abc.abstractmethod
    def decide(self, observation: dict[str, Any]) -> dict[str, float]:
        """Return target portfolio weights keyed by anonymous asset_id.

        Cash is implicit: cash_weight = 1 - sum(target_weights).
        """
        ...

    def reset(self) -> None:
        """Optional hook called before a new evaluation run."""
        pass
