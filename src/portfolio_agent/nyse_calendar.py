"""NYSE holiday and early-close calendar (execution-level, no external deps).

Computes full-holiday and early-close (1:00 PM ET) dates for U.S. equity markets,
covering the observed NYSE rules. Rules implemented:

Full holidays (closed):
- New Year's Day (Jan 1, observed)
- Martin Luther King Jr. Day (3rd Monday of January)
- Washington's Birthday / Presidents' Day (3rd Monday of February)
- Good Friday (2 days before Easter Sunday)
- Memorial Day (last Monday of May)
- Juneteenth (Jun 19, observed) -- from 2022
- Independence Day (Jul 4, observed)
- Labor Day (1st Monday of September)
- Thanksgiving (4th Thursday of November)
- Christmas (Dec 25, observed)

Early closes (1:00 PM ET):
- Day after Thanksgiving (Black Friday)
- Christmas Eve (Dec 24) when it is a weekday
- July 3 when it is a weekday (day before Independence Day)

"Observed" weekend rule: a holiday on Saturday is observed the preceding Friday;
on Sunday, the following Monday. This matches NYSE practice.

This module is dependency-free (no pandas-market-calendars) so organizers can run it
anywhere. Verify against the official NYSE holiday page before an official season.
"""

from __future__ import annotations

from datetime import date, time, timedelta
from functools import lru_cache

REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)
REGULAR_OPEN = time(9, 30)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th `weekday` (Mon=0) of `month` in `year` (n>=1)."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """The last `weekday` (Mon=0) of `month` in `year`."""
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> date:
    """Anonymous Gregorian (Meeus/Jones/Butcher) algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed(d: date) -> date:
    """Apply the NYSE weekend-observation rule."""
    if d.weekday() == 5:        # Saturday -> Friday
        return d - timedelta(days=1)
    if d.weekday() == 6:        # Sunday -> Monday
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=64)
def full_holidays(year: int) -> frozenset[date]:
    """Set of full-closure NYSE holidays for `year`."""
    hs = {
        _observed(date(year, 1, 1)),                     # New Year's Day
        _nth_weekday(year, 1, 0, 3),                      # MLK Day
        _nth_weekday(year, 2, 0, 3),                      # Presidents' Day
        _easter_sunday(year) - timedelta(days=2),         # Good Friday
        _last_weekday(year, 5, 0),                        # Memorial Day
        _observed(date(year, 7, 4)),                      # Independence Day
        _nth_weekday(year, 9, 0, 1),                      # Labor Day
        _nth_weekday(year, 11, 3, 4),                     # Thanksgiving
        _observed(date(year, 12, 25)),                    # Christmas
    }
    if year >= 2022:
        hs.add(_observed(date(year, 6, 19)))              # Juneteenth
    return frozenset(hs)


@lru_cache(maxsize=64)
def early_closes(year: int) -> frozenset[date]:
    """Set of 1:00 PM ET early-close dates for `year`."""
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    out = {thanksgiving + timedelta(days=1)}              # Black Friday
    xmas_eve = date(year, 12, 24)
    if xmas_eve.weekday() < 5:
        out.add(xmas_eve)
    jul3 = date(year, 7, 3)
    if jul3.weekday() < 5:
        out.add(jul3)
    return frozenset(out)


def is_trading_day(d: date) -> bool:
    """True if `d` is a weekday and not a full NYSE holiday."""
    return d.weekday() < 5 and d not in full_holidays(d.year)


def close_time_for(d: date) -> time:
    """Regular (16:00) or early (13:00) close for a given trading day."""
    return EARLY_CLOSE if d in early_closes(d.year) else REGULAR_CLOSE


def is_early_close(d: date) -> bool:
    return d in early_closes(d.year)
