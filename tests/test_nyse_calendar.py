"""NYSE holiday + early-close calendar (audit #2)."""

from datetime import date, time

from portfolio_agent.nyse_calendar import (
    close_time_for,
    early_closes,
    full_holidays,
    is_early_close,
    is_trading_day,
)


def test_fixed_holidays_2025():
    hs = full_holidays(2025)
    assert date(2025, 1, 1) in hs          # New Year's Day
    assert date(2025, 7, 4) in hs          # Independence Day
    assert date(2025, 12, 25) in hs        # Christmas
    assert date(2025, 6, 19) in hs         # Juneteenth


def test_floating_holidays_2025():
    hs = full_holidays(2025)
    assert date(2025, 1, 20) in hs         # MLK (3rd Mon Jan)
    assert date(2025, 5, 26) in hs         # Memorial (last Mon May)
    assert date(2025, 9, 1) in hs          # Labor (1st Mon Sep)
    assert date(2025, 11, 27) in hs        # Thanksgiving (4th Thu Nov)
    assert date(2025, 4, 18) in hs         # Good Friday 2025


def test_weekend_observation_rule():
    # 2021-07-04 was a Sunday -> observed Monday 2021-07-05
    assert date(2021, 7, 5) in full_holidays(2021)
    # 2021-12-25 was a Saturday -> observed Friday 2021-12-24
    assert date(2021, 12, 24) in full_holidays(2021)


def test_early_closes_and_close_time():
    ec = early_closes(2025)
    assert date(2025, 11, 28) in ec        # Black Friday
    assert date(2025, 12, 24) in ec        # Christmas Eve (weekday)
    assert is_early_close(date(2025, 11, 28))
    assert close_time_for(date(2025, 11, 28)) == time(13, 0)
    assert close_time_for(date(2025, 11, 26)) == time(16, 0)  # regular day


def test_is_trading_day():
    assert not is_trading_day(date(2025, 12, 25))   # Christmas
    assert not is_trading_day(date(2025, 6, 21))    # Saturday
    assert is_trading_day(date(2025, 6, 20))        # Friday, normal
