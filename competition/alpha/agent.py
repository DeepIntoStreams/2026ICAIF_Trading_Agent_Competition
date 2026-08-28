"""Example participant submission (the team's alpha).

Implements the standardized competition API: subclass BaseAgent, provide `decide`.
This example is a transparent momentum + low-volatility allocator; replace the body of
`decide` with your own strategy. Deterministic: no randomness, so it reproduces exactly.
"""

from __future__ import annotations

import math
from typing import Any

from agent_base import BaseAgent   # provided by the competition SDK (on the path at runtime)


class Agent(BaseAgent):
    agent_version = "example-momentum-1.0"

    def setup(self, universe: list[dict[str, Any]], constraints: dict[str, Any]) -> None:
        self.max_asset_weight = float(constraints.get("max_asset_weight", 0.10))
        self.max_positions = 10

    def decide(self, observation: dict[str, Any]) -> dict[str, float]:
        market = observation.get("market_features", {})
        # rank by 60-day momentum, prefer lower volatility as a tie-breaker on size
        scored = []
        for ticker, feats in market.items():
            mom = feats.get("momentum_60d")
            if mom is None or not math.isfinite(mom) or mom <= 0:
                continue
            vol = feats.get("volatility_20d") or 0.02
            scored.append((ticker, mom, max(vol, 0.005)))
        if not scored:
            return {}
        scored.sort(key=lambda x: x[1], reverse=True)
        picks = scored[: self.max_positions]

        # inverse-volatility sizing, capped, gross ~0.95
        inv = {t: 1.0 / v for t, _, v in picks}
        s = sum(inv.values())
        weights = {t: min(0.95 * inv[t] / s, self.max_asset_weight) for t in inv}
        total = sum(weights.values())
        if total > 0.95:
            weights = {t: w * 0.95 / total for t, w in weights.items()}
        return weights

    def declared_news_sources(self) -> list[str]:
        return []   # this example uses only the official observation
