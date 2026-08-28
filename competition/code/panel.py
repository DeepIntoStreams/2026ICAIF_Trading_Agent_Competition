"""Standalone per-day observation panel builder (closes gap: 'per-day observation builder').

Given the market history, fundamentals, and a news store, builds ONE trading day's shared
observation panel + official market prices from scratch -- decoupled from the batch
DailyTradingEvaluator. Reuses the exact feature/PIT functions the batch evaluator uses, so a
live-built panel matches a replayed one field for field.

  panel = PanelBuilder(data_root, news_dir).build(as_of_date)
  PanelBuilder(...).publish(out_dir, as_of_date)     # writes data/observations + data/market
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from portfolio_agent.data_loader import (
    flatten_universe, load_evaluation_universe, load_fundamentals, load_price_data)
from portfolio_agent.market_calendar import session_clock
from portfolio_agent.observation import (
    compute_fundamental_features, compute_market_features)
from portfolio_agent.point_in_time import compute_yoy_growth, latest_available_fundamentals
from portfolio_agent.news.store import NewsStore

DEFAULT_CONSTRAINTS = {
    "long_only": True, "max_asset_weight": 0.10, "max_gross_exposure": 1.00,
    "fee_rate": 0.001, "slippage_bps": 0.0,
}


def _serialize_news(rec, include_text: bool = True) -> dict[str, Any]:
    return {
        "provider": rec.provider, "provider_news_id": rec.provider_news_id,
        "published_at_utc": rec.published_at_utc.isoformat(),
        "available_at_utc": rec.available_at_utc.isoformat(),
        "ticker": rec.tickers[0] if rec.tickers else None,
        "tickers": list(rec.tickers), "company_names": list(rec.company_names),
        "headline": rec.headline if include_text else "",
        "summary": rec.summary if include_text else "",
        "source": rec.source, "url": rec.url, "content_hash": rec.content_hash,
    }


class PanelBuilder:
    def __init__(self, data_root: str, news_dir: str | None = None,
                 decision_minutes_before_close: int = 10,
                 constraints: dict[str, Any] | None = None,
                 max_news_items: int = 40):
        self.sectors = load_evaluation_universe(data_root)
        self.tickers = flatten_universe(self.sectors)
        self.sector_of = {t: sec for sec, ts in self.sectors.items() for t in ts}
        self.prices = load_price_data(data_root, self.tickers)   # has adj_open/adj_close/volume/date
        self.fundamentals = load_fundamentals(data_root, self.tickers)
        self.news = NewsStore(news_dir) if news_dir else None
        self.cutoff_minutes = decision_minutes_before_close
        self.constraints = constraints or dict(DEFAULT_CONSTRAINTS)
        self.max_news_items = max_news_items

    def _rows_upto(self, ticker: str, as_of: pd.Timestamp):
        df = self.prices.get(ticker)
        if df is None:
            return None
        d = df[df["date"] <= as_of]
        return d if not d.empty else None

    def build(self, as_of_date: str) -> dict[str, Any]:
        as_of = pd.Timestamp(as_of_date).normalize()
        clock = session_clock(as_of, self.cutoff_minutes)
        cutoff_utc = clock.decision_cutoff_utc

        market_features: dict[str, Any] = {}
        assets: list[dict[str, Any]] = []
        for t in self.tickers:
            df = self.prices.get(t)
            if df is None:
                continue
            today = df[df["date"] == as_of]
            if today.empty:
                continue                                   # not a trading day for this name
            # Features use only COMPLETED closes (strictly before as_of): the day-t close has
            # not happened at the decision cutoff. The day-t OPEN has, so it is exposed.
            hist = df[df["date"] < as_of]
            if hist.empty:
                continue
            market_features[t] = compute_market_features(
                hist["adj_close"].to_numpy(), hist["volume"].to_numpy())
            assets.append({"ticker": t, "company_name": t,
                           "sector": self.sector_of.get(t, ""),
                           "open_price": float(today.iloc[0]["adj_open"])})

        # point-in-time fundamentals (same path as the batch evaluator)
        fundamental_features: dict[str, Any] = {}
        if not self.fundamentals.empty:
            yoy = compute_yoy_growth(self.fundamentals, as_of)
            latest = latest_available_fundamentals(self.fundamentals, as_of)
            for t in [a["ticker"] for a in assets]:
                rows = latest[latest["ticker"] == t]
                if rows.empty:
                    fundamental_features[t] = compute_fundamental_features(None, as_of)
                else:
                    row = rows.iloc[0].to_dict()
                    row["revenue_yoy"] = yoy.get(t)
                    fundamental_features[t] = compute_fundamental_features(row, as_of)
        else:
            for t in [a["ticker"] for a in assets]:
                fundamental_features[t] = compute_fundamental_features(None, as_of)

        # point-in-time news (available_at <= cutoff)
        news_items: list[dict[str, Any]] = []
        if self.news is not None:
            visible = self.news.load_visible([a["ticker"] for a in assets], cutoff_utc)
            visible = visible[-self.max_news_items:] if self.max_news_items > 0 else []
            news_items = [_serialize_news(r) for r in visible]

        return {
            "session_date": as_of.date().isoformat(),
            "event_time_utc": cutoff_utc.isoformat(),
            "assets": assets,
            "market_features": market_features,
            "fundamental_features": fundamental_features,
            "constraints": dict(self.constraints),
            "news": news_items,
        }

    def market_prices(self, as_of_date: str) -> dict[str, dict[str, float]]:
        as_of = pd.Timestamp(as_of_date).normalize()
        out = {}
        for t in self.tickers:
            d = self._rows_upto(t, as_of)
            if d is not None and d.iloc[-1]["date"] == as_of:
                out[t] = {"adj_open": float(d.iloc[-1]["adj_open"]),
                          "adj_close": float(d.iloc[-1]["adj_close"])}
        return out

    def publish(self, out_dir: str, as_of_date: str) -> None:
        out = Path(out_dir)
        (out / "data" / "observations").mkdir(parents=True, exist_ok=True)
        (out / "data" / "market" / "daily").mkdir(parents=True, exist_ok=True)
        panel = self.build(as_of_date)
        (out / "data" / "observations" / f"{as_of_date}.json").write_text(json.dumps(panel))
        (out / "data" / "market" / "daily" / f"{as_of_date}.json").write_text(
            json.dumps(self.market_prices(as_of_date)))
