"""Second example team: equal-weight the 6 highest-momentum names (diversified, low-conc)."""
from __future__ import annotations
from agent_base import BaseAgent


class Agent(BaseAgent):
    agent_version = "equalweight-1.0"

    def decide(self, observation):
        market = observation.get("market_features", {})
        ranked = sorted(
            ((t, (f.get("momentum_60d") or 0.0)) for t, f in market.items()),
            key=lambda x: x[1], reverse=True)
        picks = [t for t, m in ranked if m > 0][:6]
        if not picks:
            return {}
        w = min(0.95 / len(picks), float(observation["constraints"]["max_asset_weight"]))
        return {t: w for t in picks}
