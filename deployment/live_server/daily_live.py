"""Idempotent organizer entry point for one complete live end-of-day cycle."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from datetime import date
from typing import Protocol

from .calendar_service import latest_closed_session, require_daily_live_calendar
from .market_data import import_market_day
from .store import CompetitionStore


class CompetitionPort(Protocol):
    def process_imported_day(self, database_url: str, trading_date: date) -> dict:
        """Execute, value, verify accounts, build observations, and publish."""


@dataclass(frozen=True)
class DailyResult:
    trading_date: str
    calendar_ready: bool
    imported_bars: int
    state: str
    competition_result: dict | None = None


def run_daily_live(
    database_url: str,
    trading_date: date,
    *,
    competition: CompetitionPort | None = None,
) -> DailyResult:
    store = CompetitionStore(database_url)
    require_daily_live_calendar(store, trading_date)
    imported = import_market_day(store, trading_date)
    if competition is None:
        from .daily_competition_service import DailyCompetitionService
        competition = DailyCompetitionService()
    result = competition.process_imported_day(database_url, trading_date)
    return DailyResult(trading_date.isoformat(), True, imported,
                       "competition_completed", result)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat,
                        help="exchange session date; defaults to latest closed XNYS session")
    parser.add_argument("--database-url", default=os.environ.get("COMPETITION_DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("COMPETITION_DATABASE_URL is required")
    target_date = args.date or latest_closed_session()
    result = run_daily_live(args.database_url, target_date)
    print(json.dumps(asdict(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
