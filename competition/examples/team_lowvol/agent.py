"""Inverse-volatility over the whole universe (defensive, diversified)."""
from __future__ import annotations
from agent_base import BaseAgent


class Agent(BaseAgent):
    agent_version = "lowvol-1.0"

    def decide(self, observation):
        mf = observation.get("market_features", {})
        cap = float(observation["constraints"]["max_asset_weight"])
        inv = {t: 1.0 / max(f.get("volatility_20d") or 0.02, 0.005) for t, f in mf.items()}
        s = sum(inv.values())
        if not s:
            return {}
        w = {t: min(0.90 * inv[t] / s, cap) for t in inv}
        tot = sum(w.values())
        return {t: x * 0.90 / tot for t, x in w.items()} if tot > 0.90 else w
