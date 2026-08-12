"""Starter-kit participant agent (README #3), execution level.

The minimal reference an entrant copies. It consumes a `decision_request` and returns a
schema-valid `decision_response`. The strategy here is deliberately trivial (equal-weight
the top-momentum names within the per-asset cap) - it exists to demonstrate the CONTRACT,
not to be competitive. Replace `_choose_weights` with your strategy.
"""

from __future__ import annotations

PROTOCOL_VERSION = "0.1"


class StarterKitAgent:
    def __init__(self, team_id: str = "team_demo", max_positions: int = 8):
        self.team_id = team_id
        self.max_positions = max_positions

    def _choose_weights(self, observation: dict) -> dict[str, float]:
        market = observation.get("market_features", {})
        cap = float(observation.get("constraints", {}).get("max_asset_weight", 0.30))

        # Rank by 20-day return (fallback 0), take the top N, equal-weight under the cap.
        ranked = sorted(
            market.items(),
            key=lambda kv: (kv[1].get("return_20d") or 0.0),
            reverse=True,
        )
        picks = [t for t, _ in ranked[: self.max_positions]]
        if not picks:
            return {}
        w = min(cap, 1.0 / len(picks))
        return {t: w for t in picks}

    def decide(self, request: dict) -> dict:
        obs = request["observation"]
        return {
            "type": "decision_response",
            "protocol_version": request.get("protocol_version", PROTOCOL_VERSION),
            "run_id": request["run_id"],
            "team_id": self.team_id,
            "session_date": obs["session_date"],
            "target_weights": self._choose_weights(obs),
            "metadata": {"agent_version": "starter-kit-1", "used_external_news": False},
        }
