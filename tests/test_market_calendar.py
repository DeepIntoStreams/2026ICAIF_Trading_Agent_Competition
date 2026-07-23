import pandas as pd

from portfolio_agent.config import EvaluationSettings
from portfolio_agent.market_calendar import select_evaluation_sessions, session_clock


def test_select_latest_horizon_from_available_calendar():
    calendar = pd.to_datetime(
        ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"]
    )
    selected = select_evaluation_sessions(
        calendar,
        EvaluationSettings(horizon_trading_days=3),
    )
    assert [d.date().isoformat() for d in selected] == [
        "2026-06-03",
        "2026-06-04",
        "2026-06-05",
    ]


def test_end_date_and_horizon_select_last_n_sessions():
    calendar = pd.to_datetime(
        ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"]
    )
    selected = select_evaluation_sessions(
        calendar,
        EvaluationSettings(horizon_trading_days=2, end_date="2026-06-04"),
    )
    assert [d.date().isoformat() for d in selected] == [
        "2026-06-03",
        "2026-06-04",
    ]


def test_session_clock_regular_close_cutoff():
    clock = session_clock(
        pd.Timestamp("2026-06-05"),
        decision_minutes_before_close=10,
    )
    assert clock.open_et.strftime("%H:%M") == "09:30"
    assert clock.decision_cutoff_et.strftime("%H:%M") == "15:50"
    assert clock.close_et.strftime("%H:%M") == "16:00"
