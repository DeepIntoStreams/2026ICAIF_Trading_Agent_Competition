"""The standardized participant Agent API (ACM ICAIF 2026 Trading Agent Competition).

Every submission MUST provide a subclass of `BaseAgent` named `Agent` in `agent.py`.
This is the ONLY contract between a team's alpha and the competition server:

    input  : one `observation` dict per trading day (see schemas/observation_*.schema.json)
    output : `target_weights` -> {ticker: weight}, executed at the NEXT market open

Rules the contract enforces:
  * Determinism / reproducibility. Given the same observation and the same frozen code,
    `decide` must return the same weights. Seed any randomness from `setup(...)`'s config.
    The organizer re-runs top teams' agents on the recorded observations and checks the
    weights match; non-reproducible submissions are disqualified.
  * No look-ahead. The observation already contains only point-in-time data (every news
    item's available_at_utc <= the decision cutoff). Do NOT fetch same-day-or-future prices
    inside decide(); only public news predating the cutoff may be added from your own store.
  * Feasibility. Return long-only weights, <= max_asset_weight per name, gross <= 1.0.
    Out-of-range weights are repaired by the server and counted as M9 violations.
  * Time budget. decide() must return within the per-day wall-clock limit (see RULES.md);
    a timeout or crash makes the server retain your previous day's weights.
"""

from __future__ import annotations

from typing import Any


class BaseAgent:
    """Subclass this. Only `decide` is required; `setup` is optional."""

    #: bump this in your own agent for your records; the server logs it for the audit trail
    agent_version: str = "0.0.0"

    def setup(self, universe: list[dict[str, Any]], constraints: dict[str, Any]) -> None:
        """Called once before the first trading day.

        universe:    list of {ticker, company_name, sector}
        constraints: {long_only, max_asset_weight, max_gross_exposure, fee_rate, ...}
        Load models, set seeds, and cache universe/constraints here.
        """
        return None

    def decide(self, observation: dict[str, Any]) -> dict[str, float]:
        """Return target weights for the NEXT trading day.

        observation keys (see the schemas): session_date, event_time_utc, assets,
        market_features, fundamental_features, portfolio, constraints, news.
        Return {ticker: weight}; unallocated capital stays in cash.
        """
        raise NotImplementedError

    # -- optional hook: declare which external public news sources you used (audited) --
    def declared_news_sources(self) -> list[str]:
        return []
