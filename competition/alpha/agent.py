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
        self.max_asset_weight = float(constraints.get("max_asset_weight", 0.30))
        self.max_positions = 10

    def decide(self, observation: dict[str, Any]) -> dict[str, float]:
        # The observation gives RAW daily bars (OHLCV) - engineer your own features from them.
        history = observation.get("market_history", {})
        scored = []
        for ticker, bars in history.items():
            closes = [b["close"] for b in bars if b.get("close")]
            if len(closes) < 61:
                continue
            mom = closes[-1] / closes[-61] - 1.0                 # 60-day momentum
            if not math.isfinite(mom) or mom <= 0:
                continue
            rets = [closes[i] / closes[i - 1] - 1.0               # 20-day volatility
                    for i in range(len(closes) - 20, len(closes)) if closes[i - 1] > 0]
            if len(rets) > 1:
                mean_r = sum(rets) / len(rets)
                vol = (sum((r - mean_r) ** 2 for r in rets) / len(rets)) ** 0.5
            else:
                vol = 0.02
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
