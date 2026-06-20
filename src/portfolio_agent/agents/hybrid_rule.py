"""Deterministic hybrid rule-based agent.

Scoring formula from the technical report:
  technical_i = [0.6 * momentum20_i + 0.4 * momentum60_i] / max(vol20_i, eps)
  fundamental_i = 0.25*z(revenue_yoy) + 0.25*z(fcf_margin)
                + 0.20*z(net_margin) + 0.15*z(roe) - 0.15*z(debt_to_assets)
  final_score_i = 0.70*z(technical) + 0.30*z(fundamental)

Every rebalance_frequency days: keep positive-score assets above SMA50,
select at most max_positions, weight proportional to score/vol, project
to the long-only capped simplex.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .base import BaseAgent


def _cross_sectional_zscore(values: dict[str, float | None]) -> dict[str, float]:
    valid = {k: v for k, v in values.items() if v is not None and np.isfinite(v)}
    if len(valid) < 2:
        return {k: 0.0 for k in values}
    arr = np.array(list(valid.values()))
    mu, sigma = float(np.mean(arr)), float(np.std(arr, ddof=1))
    if sigma < 1e-12:
        return {k: 0.0 for k in values}
    result = {k: 0.0 for k in values}
    for k, v in valid.items():
        result[k] = (v - mu) / sigma
    return result


class HybridRuleAgent(BaseAgent):

    def __init__(
        self,
        rebalance_frequency: int = 5,
        max_positions: int = 8,
        technical_weight: float = 0.70,
        fundamental_weight: float = 0.30,
        momentum_short_weight: float = 0.60,
        momentum_long_weight: float = 0.40,
        max_asset_weight: float = 0.30,
    ):
        self.rebalance_frequency = rebalance_frequency
        self.max_positions = max_positions
        self.technical_weight = technical_weight
        self.fundamental_weight = fundamental_weight
        self.momentum_short_weight = momentum_short_weight
        self.momentum_long_weight = momentum_long_weight
        self.max_asset_weight = max_asset_weight
        self._last_weights: dict[str, float] = {}
        self._step_count = 0

    def reset(self) -> None:
        self._last_weights = {}
        self._step_count = 0

    def decide(self, observation: dict[str, Any]) -> dict[str, float]:
        self._step_count += 1

        if (
            self._step_count % self.rebalance_frequency != 1
            and self._step_count > 1
            and self._last_weights
        ):
            return dict(self._last_weights)

        market = observation.get("market_features", {})
        fundamentals = observation.get("fundamental_features", {})
        asset_ids = list(market.keys())

        if not asset_ids:
            return {}

        eps = 1e-8

        mom20: dict[str, float | None] = {}
        mom60: dict[str, float | None] = {}
        vol20: dict[str, float | None] = {}
        sma_dist: dict[str, float | None] = {}

        for aid in asset_ids:
            mf = market.get(aid, {})
            mom20[aid] = mf.get("return_20d")
            mom60[aid] = mf.get("momentum_60d")
            vol20[aid] = mf.get("volatility_20d")
            sma_dist[aid] = mf.get("sma_distance_50")

        tech_raw: dict[str, float | None] = {}
        for aid in asset_ids:
            m20 = mom20[aid]
            m60 = mom60[aid]
            v = vol20[aid]
            if m20 is not None and m60 is not None and v is not None:
                blended = self.momentum_short_weight * m20 + self.momentum_long_weight * m60
                tech_raw[aid] = blended / max(v, eps)
            else:
                tech_raw[aid] = None

        rev_yoy: dict[str, float | None] = {}
        fcf_m: dict[str, float | None] = {}
        net_m: dict[str, float | None] = {}
        roe: dict[str, float | None] = {}
        d2a: dict[str, float | None] = {}

        for aid in asset_ids:
            ff = fundamentals.get(aid, {})
            rev_yoy[aid] = ff.get("revenue_yoy")
            fcf_m[aid] = ff.get("fcf_margin")
            net_m[aid] = ff.get("net_margin")
            roe[aid] = ff.get("roe")
            d2a[aid] = ff.get("debt_to_assets")

        z_rev = _cross_sectional_zscore(rev_yoy)
        z_fcf = _cross_sectional_zscore(fcf_m)
        z_net = _cross_sectional_zscore(net_m)
        z_roe = _cross_sectional_zscore(roe)
        z_d2a = _cross_sectional_zscore(d2a)

        fund_raw: dict[str, float] = {}
        for aid in asset_ids:
            fund_raw[aid] = (
                0.25 * z_rev[aid]
                + 0.25 * z_fcf[aid]
                + 0.20 * z_net[aid]
                + 0.15 * z_roe[aid]
                - 0.15 * z_d2a[aid]
            )

        z_tech = _cross_sectional_zscore(tech_raw)
        z_fund = _cross_sectional_zscore(fund_raw)

        final_score: dict[str, float] = {}
        for aid in asset_ids:
            final_score[aid] = (
                self.technical_weight * z_tech[aid]
                + self.fundamental_weight * z_fund[aid]
            )

        eligible: list[tuple[str, float]] = []
        for aid in asset_ids:
            sd = sma_dist.get(aid)
            if final_score[aid] > 0 and sd is not None and sd > 0:
                eligible.append((aid, final_score[aid]))

        if not eligible:
            self._last_weights = {}
            return {}

        eligible.sort(key=lambda x: x[1], reverse=True)
        selected = eligible[: self.max_positions]

        raw_w: dict[str, float] = {}
        for aid, score in selected:
            v = vol20.get(aid)
            divisor = max(v, eps) if v is not None else eps
            raw_w[aid] = score / divisor

        total = sum(raw_w.values())
        if total <= 0:
            self._last_weights = {}
            return {}

        weights = {aid: w / total for aid, w in raw_w.items()}

        weights = {aid: min(w, self.max_asset_weight) for aid, w in weights.items()}
        total = sum(weights.values())
        if total > 1.0:
            weights = {aid: w / total for aid, w in weights.items()}

        self._last_weights = weights
        return weights
