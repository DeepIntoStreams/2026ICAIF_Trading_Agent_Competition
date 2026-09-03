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
from portfolio_agent.news.store import NewsStore

# raw quarterly statement line items exposed point-in-time (no engineered ratios)
_FUNDAMENTAL_FIELDS = ["revenue", "net_income", "operating_income", "total_assets",
                       "total_debt", "stockholders_equity", "free_cash_flow", "operating_cash_flow"]

DEFAULT_CONSTRAINTS = {
    "long_only": True, "max_asset_weight": 0.30, "max_gross_exposure": 1.00,
    "fee_rate": 0.001, "slippage_bps": 0.0,
}   # max_asset_weight is the per-asset cap knob (set 0.10 or 0.30)


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
                 max_news_items: int = 40,
                 history_window: int = 120):
        self.sectors = load_evaluation_universe(data_root)
        self.tickers = flatten_universe(self.sectors)
        self.sector_of = {t: sec for sec, ts in self.sectors.items() for t in ts}
        self.prices = load_price_data(data_root, self.tickers)   # has adj_open/adj_close/volume/date
        self.fundamentals = load_fundamentals(data_root, self.tickers)
        self.news = NewsStore(news_dir) if news_dir else None
        self.cutoff_minutes = decision_minutes_before_close
        self.constraints = constraints or dict(DEFAULT_CONSTRAINTS)
        self.max_news_items = max_news_items
        self.history_window = history_window

    def _rows_upto(self, ticker: str, as_of: pd.Timestamp):
        df = self.prices.get(ticker)
        if df is None:
            return None
        d = df[df["date"] <= as_of]
        return d if not d.empty else None

    def build(self, as_of_date: str) -> dict[str, Any]:
        as_of = pd.Timestamp(as_of_date).normalize()
        # Decision cutoff = 9:00 AM ET on the trading day (30 min before the 9:30 open).
        cutoff_utc = (pd.Timestamp(as_of.date()).tz_localize("America/New_York")
                      .replace(hour=9, minute=0).tz_convert("UTC"))

        market_history: dict[str, Any] = {}
        assets: list[dict[str, Any]] = []
        for t in self.tickers:
            df = self.prices.get(t)
            if df is None:
                continue
            today = df[df["date"] == as_of]
            if today.empty:
                continue                                   # not a trading day for this name
            # Features use only COMPLETED closes strictly before as_of (through the previous
            # trading day). The decision is made at the 9:00 AM ET cutoff, before the 9:30 open,
            # so NO day-t price (not even the open) is exposed to the agent.
            hist = df[df["date"] < as_of]
            if hist.empty:
                continue
            # Raw completed daily bars (adjusted OHLCV) through t-1 - a rolling window; agents
            # build their own features. All bars are strictly before the session date.
            h = hist.tail(self.history_window)
            market_history[t] = [
                {"date": str(pd.Timestamp(d).date()),
                 "open": round(float(o), 6), "high": round(float(hi), 6),
                 "low": round(float(lo), 6), "close": round(float(c), 6),
                 "volume": float(v)}
                for d, o, hi, lo, c, v in zip(h["date"], h["adj_open"], h["adj_high"],
                                              h["adj_low"], h["adj_close"], h["volume"])
            ]
            assets.append({"ticker": t, "company_name": t,
                           "sector": self.sector_of.get(t, "")})

        # Raw point-in-time fundamentals: the latest quarterly statement available by the cutoff
        # (available_at <= cutoff) - raw reported line items, no engineered ratios.
        fundamentals: dict[str, Any] = {}
        if not self.fundamentals.empty:
            avail_at = pd.to_datetime(self.fundamentals["available_at"], utc=True)
            avail = self.fundamentals[avail_at <= cutoff_utc]
            for t in [a["ticker"] for a in assets]:
                rows = avail[avail["ticker"] == t]
                if rows.empty:
                    continue
                row = rows.sort_values(["period_end", "available_at"]).iloc[-1]
                rec = {"period_end": str(pd.Timestamp(row["period_end"]).date())}
                for field in _FUNDAMENTAL_FIELDS:
                    v = row.get(field)
                    rec[field] = None if v is None or pd.isna(v) else float(v)
                fundamentals[t] = rec

        # The official observation carries NO news: news is participant-collected (teams add
        # their own eligible public sources). See the Data and Terms pages.
        news_items: list[dict[str, Any]] = []

        return {
            "session_date": as_of.date().isoformat(),
            "event_time_utc": cutoff_utc.isoformat(),
            "assets": assets,
            "market_history": market_history,
            "fundamentals": fundamentals,
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
