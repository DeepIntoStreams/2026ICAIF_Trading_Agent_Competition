"""Disciplined cross-sectional multi-signal allocator (transparent rule-based agent).

Design goals (in order): valid, reproducible, disciplined, practical. NOT a return-
maximiser -- the competition ranks agents by AVERAGE RANK across M1-M9, so turnover (M8),
violations (M9), drawdown (M5) and risk-adjusted return (M3/M4) matter as much as raw
return. A steady, diversified, low-turnover book beats a concentrated moonshot on average
rank. Every parameter is named in `AllocatorParams` so the whole policy is auditable and
reproducible; there is no randomness and no tuning against the eval metric.

Pipeline each day (all data point-in-time, taken from the observation the server sends):
  1. signals   -> momentum, trend, low-vol, fundamental quality, news sentiment
  2. z-score    -> each signal standardised cross-sectionally (missing -> 0)
  3. composite  -> fixed documented weights (not fitted to the metric)
  4. select     -> top-K by score, <= per-sector cap, drop score < 0
  5. size       -> inverse-volatility among picks, per-name cap (at the 10% limit)
  6. risk       -> gross exposure scaled down while in drawdown (hold cash, don't bet big)
  7. smooth     -> blend toward previous weights + no-trade band (low turnover)
  8. validate   -> clip/cap/scale so the action is always feasible (M9 = 0 by construction)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from portfolio_agent.news.sentiment import NEGATIVE_WORDS, POSITIVE_WORDS
from portfolio_agent.risk import sanitize_target_weights


@dataclass
class AllocatorParams:
    # -- composite signal weights (sum to 1.0; interpretable, not metric-tuned) --
    w_momentum: float = 0.30
    w_quality: float = 0.25
    w_lowvol: float = 0.20
    w_trend: float = 0.15
    w_news: float = 0.10
    # -- selection --
    max_positions: int = 12          # breadth: hold a diversified book, not a few bets
    max_per_sector: int = 3          # force spread across the 6 sectors
    min_score: float = 0.0           # never hold below-average names
    # -- sizing --
    per_asset_cap: float = 0.10      # at the 10% hard limit: "do not bet big"
    vol_floor: float = 0.005         # floor on daily vol used for inverse-vol sizing
    # -- risk overlay (drawdown de-risking) --
    base_gross: float = 0.90         # never fully invested; keep a cash buffer
    dd_derisk_start: float = 0.05    # begin cutting exposure at -5% drawdown
    dd_derisk_full: float = 0.15     # exposure floor reached at -15% drawdown
    gross_floor: float = 0.40        # smallest gross exposure when deep in drawdown
    # -- turnover control --
    smoothing: float = 0.50          # blend 50% toward target each day (anti-whipsaw)
    no_trade_band: float = 0.02      # ignore per-name changes smaller than 2%
    min_position: float = 0.01       # drop dust positions


def _zscore(values: dict[str, float | None]) -> dict[str, float]:
    present = [v for v in values.values() if v is not None and math.isfinite(v)]
    if len(present) < 2:
        return {k: 0.0 for k in values}
    mean = sum(present) / len(present)
    var = sum((v - mean) ** 2 for v in present) / len(present)
    std = math.sqrt(var)
    if std == 0:
        return {k: 0.0 for k in values}
    return {
        k: ((v - mean) / std if v is not None and math.isfinite(v) else 0.0)
        for k, v in values.items()
    }


def _news_sentiment(observation: dict[str, Any], tickers: list[str]) -> dict[str, float]:
    totals: dict[str, float] = {t: 0.0 for t in tickers}
    counts: dict[str, int] = {t: 0 for t in tickers}
    for item in observation.get("news", []):
        text = f"{item.get('headline', '')} {item.get('summary', '')}".lower()
        words = text.split()
        s = sum(w.strip(".,!?:;") in POSITIVE_WORDS for w in words) - \
            sum(w.strip(".,!?:;") in NEGATIVE_WORDS for w in words)
        tags = item.get("tickers") or ([item.get("ticker")] if item.get("ticker") else [])
        for t in tags:
            if t in totals:
                totals[t] += s
                counts[t] += 1
    return {t: (totals[t] / counts[t] if counts[t] else None) for t in tickers}


class DisciplinedAllocator:
    """Transparent rule-based portfolio agent. See module docstring."""

    def __init__(self, params: AllocatorParams | None = None,
                 max_asset_weight: float = 0.10, max_gross_exposure: float = 1.00):
        self.p = params or AllocatorParams()
        self.max_asset_weight = max_asset_weight
        self.max_gross_exposure = max_gross_exposure
        self.prev_weights: dict[str, float] = {}
        self.peak_nav: float | None = None
        self.last_diagnostics: dict[str, Any] = {}

    def reset(self) -> None:
        self.prev_weights = {}
        self.peak_nav = None
        self.last_diagnostics = {}

    # allow the harness to call observe() as a no-op if it does
    def observe(self, *args: Any, **kwargs: Any) -> None:
        return None

    def _drawdown_scaler(self, nav: float) -> float:
        if not nav or nav <= 0:
            return 1.0
        self.peak_nav = nav if self.peak_nav is None else max(self.peak_nav, nav)
        dd = nav / self.peak_nav - 1.0          # <= 0
        p = self.p
        if dd >= -p.dd_derisk_start:
            return 1.0
        if dd <= -p.dd_derisk_full:
            return p.gross_floor
        # linear ramp between start and full
        frac = (-dd - p.dd_derisk_start) / (p.dd_derisk_full - p.dd_derisk_start)
        return 1.0 - frac * (1.0 - p.gross_floor)

    def decide(self, observation: dict[str, Any]) -> dict[str, float]:
        p = self.p
        assets = observation.get("assets", [])
        sector = {str(a["ticker"]): str(a.get("sector", "")) for a in assets}
        tickers = [str(a["ticker"]) for a in assets] or \
            list(observation.get("market_features", {}).keys())
        mf = observation.get("market_features", {})
        ff = observation.get("fundamental_features", {})

        # 1) raw signals
        mom = {t: (mf.get(t, {}).get("momentum_60d")
                   if mf.get(t, {}).get("momentum_60d") is not None
                   else mf.get(t, {}).get("return_20d")) for t in tickers}
        trend = {t: mf.get(t, {}).get("sma_distance_50") for t in tickers}
        vol = {t: mf.get(t, {}).get("volatility_20d") for t in tickers}
        lowvol = {t: (-vol[t] if vol[t] is not None else None) for t in tickers}
        news = _news_sentiment(observation, tickers)

        z_mom, z_trend, z_lowvol, z_news = (
            _zscore(mom), _zscore(trend), _zscore(lowvol), _zscore(news))
        # quality = average of standardised fundamental signals (leverage inverted)
        z_nm = _zscore({t: ff.get(t, {}).get("net_margin") for t in tickers})
        z_roe = _zscore({t: ff.get(t, {}).get("roe") for t in tickers})
        z_lev = _zscore({t: ff.get(t, {}).get("debt_to_assets") for t in tickers})
        z_gro = _zscore({t: ff.get(t, {}).get("revenue_yoy") for t in tickers})
        z_qual = {t: (z_nm[t] + z_roe[t] - z_lev[t] + z_gro[t]) / 4.0 for t in tickers}

        # 2/3) composite score
        score = {t: (p.w_momentum * z_mom[t] + p.w_trend * z_trend[t]
                     + p.w_lowvol * z_lowvol[t] + p.w_quality * z_qual[t]
                     + p.w_news * z_news[t]) for t in tickers}

        # 4) selection: top-K, sector-capped, score > min
        picks: list[str] = []
        per_sector: dict[str, int] = {}
        for t in sorted(tickers, key=lambda x: score[x], reverse=True):
            if score[t] <= p.min_score:
                break
            sec = sector.get(t, "")
            if per_sector.get(sec, 0) >= p.max_per_sector:
                continue
            picks.append(t)
            per_sector[sec] = per_sector.get(sec, 0) + 1
            if len(picks) >= p.max_positions:
                break

        # 5) inverse-volatility sizing with per-name cap
        target: dict[str, float] = {}
        if picks:
            iv = {t: 1.0 / max(vol.get(t) or p.vol_floor, p.vol_floor) for t in picks}
            s = sum(iv.values())
            w = {t: iv[t] / s for t in picks}
            # cap and renormalise (two passes is enough for a single cap level)
            for _ in range(3):
                w = {t: min(x, p.per_asset_cap) for t, x in w.items()}
                tot = sum(w.values())
                if tot > 0:
                    w = {t: x / tot for t, x in w.items()}
            target = w

        # 6) risk overlay: scale gross by drawdown state
        gross = p.base_gross * self._drawdown_scaler(
            float(observation.get("portfolio", {}).get("nav", 0.0)))
        target = {t: x * gross for t, x in target.items()}

        # 7) smoothing + no-trade band vs previous book (low turnover)
        prev = self.prev_weights
        final: dict[str, float] = {}
        for t in set(target) | set(prev):
            tgt, pv = target.get(t, 0.0), prev.get(t, 0.0)
            blended = pv + p.smoothing * (tgt - pv)
            if abs(blended - pv) < p.no_trade_band:
                blended = pv
            if blended >= p.min_position:
                final[t] = blended

        # 8) validate -> always feasible
        allowed = tickers
        sanitized, violations = sanitize_target_weights(
            final, allowed, self.max_asset_weight, self.max_gross_exposure)
        self.prev_weights = dict(sanitized)
        self.last_diagnostics = {
            "n_picks": len(picks), "gross": round(sum(sanitized.values()), 4),
            "violations": violations,
        }
        return sanitized

    @property
    def diagnostics(self) -> dict[str, Any]:
        return self.last_diagnostics
