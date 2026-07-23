"""Build anonymized agent-safe observations from private evaluator state."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from .news.models import NewsRecord
from .security import assert_agent_safe_observation, assert_observation_point_in_time


def _safe_float(v: float) -> float | None:
    if v is None or not math.isfinite(v):
        return None
    return float(v)


def compute_market_features(
    closes: np.ndarray,
    volumes: np.ndarray,
) -> dict[str, float | None]:
    """Compute agent-safe market features from adjusted close and volume arrays."""
    n = len(closes)
    if n == 0:
        return {}

    base = closes[0]
    cur = closes[-1]

    def _ret(lag: int) -> float | None:
        if n <= lag or closes[-(lag + 1)] == 0:
            return None
        return _safe_float(cur / closes[-(lag + 1)] - 1.0)

    relative_price = _safe_float(cur / base) if base != 0 else None

    sma_distance = None
    if n >= 50:
        sma50 = float(np.mean(closes[-50:]))
        if sma50 > 0:
            sma_distance = _safe_float(cur / sma50 - 1.0)

    vol_20d = None
    if n >= 21:
        rets = np.diff(closes[-21:]) / np.where(closes[-21:-1] == 0, np.nan, closes[-21:-1])
        valid_rets = rets[np.isfinite(rets)]
        if len(valid_rets) > 1:
            vol_20d = _safe_float(float(np.std(valid_rets, ddof=1)))

    volume_ratio = None
    volume_zscore = None
    if n >= 20:
        v20 = volumes[-20:]
        v_mean = float(np.mean(v20))
        v_std = float(np.std(v20, ddof=1))
        if v_mean > 0:
            volume_ratio = _safe_float(volumes[-1] / v_mean)
        if v_std > 0:
            volume_zscore = _safe_float((volumes[-1] - v_mean) / v_std)

    return {
        "relative_price": relative_price,
        "return_1d": _ret(1),
        "return_5d": _ret(5),
        "return_20d": _ret(20),
        "momentum_60d": _ret(60),
        "sma_distance_50": sma_distance,
        "volatility_20d": vol_20d,
        "volume_ratio_20": volume_ratio,
        "volume_zscore_20": volume_zscore,
    }


def compute_fundamental_features(
    fundamentals_row: dict[str, Any] | None,
    cutoff_date: Any,
    previous_available_at: Any | None = None,
) -> dict[str, Any]:
    """Derive agent-safe fundamental ratios from a single point-in-time row."""
    if fundamentals_row is None or not fundamentals_row:
        return {
            "net_margin": None,
            "operating_margin": None,
            "roe": None,
            "fcf_margin": None,
            "debt_to_assets": None,
            "cash_conversion": None,
            "revenue_yoy": None,
            "report_age_days": None,
            "is_new_report": False,
            "stale_fundamental": True,
        }

    def safe_div(a: Any, b: Any) -> float | None:
        try:
            a, b = float(a), float(b)
        except (TypeError, ValueError):
            return None
        if b == 0 or not math.isfinite(a) or not math.isfinite(b):
            return None
        return _safe_float(a / b)

    revenue = fundamentals_row.get("revenue")
    net_income = fundamentals_row.get("net_income")
    operating_income = fundamentals_row.get("operating_income")
    total_assets = fundamentals_row.get("total_assets")
    total_debt = fundamentals_row.get("total_debt")
    equity = fundamentals_row.get("stockholders_equity")
    fcf = fundamentals_row.get("free_cash_flow")
    ocf = fundamentals_row.get("operating_cash_flow")
    revenue_yoy = fundamentals_row.get("revenue_yoy")

    available_at = fundamentals_row.get("available_at")
    period_end = fundamentals_row.get("period_end")

    def _tz_naive(ts: Any) -> pd.Timestamp | None:
        try:
            t = pd.Timestamp(ts)
            return t.tz_localize(None) if t.tzinfo is not None else t
        except Exception:
            return None

    age_days = None
    cutoff_ts = _tz_naive(cutoff_date)
    if cutoff_ts is not None:
        ref_ts = _tz_naive(available_at) or _tz_naive(period_end)
        if ref_ts is not None:
            age_days = (cutoff_ts - ref_ts).days

    is_new = False
    if available_at is not None and previous_available_at is not None:
        try:
            is_new = pd.Timestamp(available_at) != pd.Timestamp(previous_available_at)
        except Exception:
            pass

    stale = False
    if age_days is not None and age_days > 180:
        stale = True

    return {
        "net_margin": safe_div(net_income, revenue),
        "operating_margin": safe_div(operating_income, revenue),
        "roe": safe_div(net_income, equity),
        "fcf_margin": safe_div(fcf, revenue),
        "debt_to_assets": safe_div(total_debt, total_assets),
        "cash_conversion": safe_div(ocf, net_income),
        "revenue_yoy": _safe_float(float(revenue_yoy)) if revenue_yoy is not None else None,
        "report_age_days": age_days,
        "is_new_report": is_new,
        "stale_fundamental": stale,
    }


def build_observation(
    step_id: int,
    market_features: dict[str, dict[str, float | None]],
    fundamental_features: dict[str, dict[str, Any]],
    portfolio_weights: dict[str, float],
    cash_ratio: float,
    nav_ratio: float,
    drawdown: float,
    constraints: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the full agent-safe observation dict."""
    observation: dict[str, Any] = {
        "step_id": int(step_id),
        "market_features": market_features,
        "fundamental_features": fundamental_features,
        "portfolio": {
            "weights": dict(portfolio_weights),
            "cash_ratio": float(cash_ratio),
            "nav_ratio": float(nav_ratio),
            "drawdown": float(drawdown),
        },
        "constraints": dict(constraints),
    }
    assert_agent_safe_observation(observation)
    return observation


def _serialize_news(
    record: NewsRecord,
    include_raw_text: bool = True,
) -> dict[str, Any]:
    return {
        "provider": record.provider,
        "provider_news_id": record.provider_news_id,
        "published_at_utc": record.published_at_utc.isoformat(),
        "available_at_utc": record.available_at_utc.isoformat(),
        "ticker": record.tickers[0] if record.tickers else None,
        "tickers": list(record.tickers),
        "company_names": list(record.company_names),
        "headline": record.headline if include_raw_text else "",
        "summary": record.summary if include_raw_text else "",
        "source": record.source,
        "url": record.url,
        "content_hash": record.content_hash,
    }


def build_decision_observation(
    session_date: str,
    event_time_utc: datetime,
    universe: list[dict[str, Any]],
    open_prices: dict[str, float],
    market_features: dict[str, dict[str, Any]],
    fundamental_features: dict[str, dict[str, Any]],
    portfolio: dict[str, Any],
    constraints: dict[str, Any],
    news: list[NewsRecord],
    max_news_items: int,
    include_raw_text: bool = True,
) -> dict[str, Any]:
    """Build a ticker-aware point-in-time decision observation."""
    visible_news = [
        record for record in news
        if record.available_at_utc <= event_time_utc
    ]
    visible_news.sort(
        key=lambda record: (
            record.available_at_utc,
            record.provider,
            record.provider_news_id,
        )
    )
    capped_news = visible_news[-max_news_items:] if max_news_items > 0 else []

    assets: list[dict[str, Any]] = []
    for item in universe:
        ticker = str(item["ticker"])
        asset = dict(item)
        asset["open_price"] = open_prices.get(ticker)
        assets.append(asset)

    observation: dict[str, Any] = {
        "session_date": session_date,
        "event_time_utc": event_time_utc.isoformat(),
        "assets": assets,
        "market_features": market_features,
        "fundamental_features": fundamental_features,
        "portfolio": dict(portfolio),
        "constraints": dict(constraints),
        "news": [
            _serialize_news(record, include_raw_text=include_raw_text)
            for record in capped_news
        ],
    }
    assert_observation_point_in_time(observation, event_time_utc)
    return observation
