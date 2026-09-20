"""Replace this mock policy with the team's own agent implementation."""

from __future__ import annotations


class Agent:
    agent_version = "mock-0.1"

    def decide(self, observation: dict) -> dict[str, float]:
        """A deliberately trivial example: put 5% in the first available asset."""
        assets = observation.get("assets", [])
        return {assets[0]["ticker"]: 0.05} if assets else {}

