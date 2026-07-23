"""Market-session selection and NYSE regular-session clocks."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from .config import EvaluationSettings
from .events import SessionClock


ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def _normalize_session_dates(calendar: Iterable[pd.Timestamp]) -> list[pd.Timestamp]:
    dates = pd.to_datetime(list(calendar))
    normalized = sorted({pd.Timestamp(value).normalize() for value in dates})
    return normalized


def select_evaluation_sessions(
    calendar: Iterable[pd.Timestamp],
    settings: EvaluationSettings,
) -> list[pd.Timestamp]:
    sessions = _normalize_session_dates(calendar)
    if not sessions:
        raise ValueError("Market calendar is empty")

    if settings.start_date:
        start = pd.Timestamp(settings.start_date).normalize()
        sessions = [session for session in sessions if session >= start]

    if settings.end_date:
        end = pd.Timestamp(settings.end_date).normalize()
        sessions = [session for session in sessions if session <= end]

    if not sessions:
        raise ValueError("No market sessions match the configured date window")

    if settings.start_date and settings.end_date:
        return sessions

    horizon = int(settings.horizon_trading_days)
    if horizon <= 0:
        raise ValueError("horizon_trading_days must be positive")

    if settings.start_date and not settings.end_date:
        return sessions[:horizon]

    return sessions[-horizon:]


def session_clock(
    session_date: pd.Timestamp,
    decision_minutes_before_close: int,
    close_time_et: time | None = None,
) -> SessionClock:
    date_value = pd.Timestamp(session_date).date()
    close_t = close_time_et or time(16, 0)
    open_et = datetime.combine(date_value, time(9, 30), tzinfo=ET)
    close_et = datetime.combine(date_value, close_t, tzinfo=ET)
    cutoff_et = close_et - timedelta(minutes=decision_minutes_before_close)

    return SessionClock(
        session_date=date_value.isoformat(),
        open_et=open_et,
        open_utc=open_et.astimezone(UTC),
        decision_cutoff_et=cutoff_et,
        decision_cutoff_utc=cutoff_et.astimezone(UTC),
        close_et=close_et,
        close_utc=close_et.astimezone(UTC),
    )

