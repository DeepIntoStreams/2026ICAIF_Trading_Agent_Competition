"""Event dataclasses for daily market simulation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from portfolio_agent.news.models import NewsRecord


@dataclass(frozen=True)
class SessionClock:
    session_date: str
    open_et: datetime
    open_utc: datetime
    decision_cutoff_et: datetime
    decision_cutoff_utc: datetime
    close_et: datetime
    close_utc: datetime


@dataclass(frozen=True)
class MarketOpenEvent:
    session_date: str
    event_time_utc: datetime
    open_prices: dict[str, float]


@dataclass(frozen=True)
class NewsEvent:
    session_date: str
    event_time_utc: datetime
    record: NewsRecord


@dataclass(frozen=True)
class DecisionEvent:
    session_date: str
    event_time_utc: datetime


@dataclass(frozen=True)
class MarketCloseEvent:
    session_date: str
    event_time_utc: datetime
    close_prices: dict[str, float]

