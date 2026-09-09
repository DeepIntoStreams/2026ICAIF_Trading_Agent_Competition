#!/usr/bin/env python3
"""Initialize schema, stock universe, XNYS sessions, and optional market bars."""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, timedelta
from pathlib import Path

import psycopg

from deployment.bootstrap.common import (
    DEFAULT_CONFIG,
    load_config,
    safe_database_target,
    write_run_record,
)
from deployment.live_server.calendar_service import ensure_trading_days, latest_closed_session
from deployment.live_server.market_data import import_market_day
from data.database.init_db import initialize
from deployment.live_server.store import CompetitionStore, now_utc


def monday_of_week(day: date) -> date:
    return day - timedelta(days=day.weekday())


def upsert_instruments(database_url: str, instruments: list[dict]) -> int:
    timestamp = now_utc()
    with psycopg.connect(database_url) as connection:
        for item in instruments:
            connection.execute(
                """INSERT INTO instruments
                       (ticker, company_name, sector, exchange, currency, is_active, created_at)
                   VALUES (%s, %s, %s, 'NYSE/NASDAQ', 'USD', TRUE, %s)
                   ON CONFLICT (ticker) DO UPDATE SET
                       company_name=EXCLUDED.company_name,
                       sector=EXCLUDED.sector,
                       exchange=EXCLUDED.exchange,
                       currency=EXCLUDED.currency,
                       is_active=TRUE""",
                (item["ticker"], item["company_name"], item["sector"], timestamp),
            )
        active_count = connection.execute(
            "SELECT count(*) FROM instruments WHERE is_active"
        ).fetchone()[0]
    return int(active_count)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("COMPETITION_DATABASE_URL"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument(
        "--fetch-market-data",
        action="store_true",
        help="download complete daily bars for all closed sessions in the range",
    )
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("COMPETITION_DATABASE_URL is required")

    config = load_config(args.config)
    latest_closed = latest_closed_session(calendar_name=config["calendar"])
    start = args.start_date or monday_of_week(latest_closed)
    end = args.end_date or latest_closed
    if end < start:
        raise SystemExit("--end-date must not be before --start-date")
    if args.fetch_market_data and end > latest_closed:
        raise SystemExit(
            f"cannot fetch an unclosed session; latest closed XNYS session is {latest_closed}"
        )

    initialize(args.database_url)
    active_count = upsert_instruments(args.database_url, config["instruments"])
    if active_count != len(config["instruments"]):
        raise RuntimeError(
            f"database has {active_count} active instruments; config has "
            f"{len(config['instruments'])}. Deactivate removed instruments explicitly."
        )

    store = CompetitionStore(args.database_url)
    sessions = ensure_trading_days(
        store, start, end, calendar_name=config["calendar"]
    )
    imported: dict[str, int] = {}
    if args.fetch_market_data:
        for session in sessions:
            imported[session.isoformat()] = import_market_day(store, session)

    summary = {
        "database": safe_database_target(args.database_url),
        "config_sha256": config["_config_sha256"],
        "active_instruments": active_count,
        "calendar": config["calendar"],
        "calendar_start": start.isoformat(),
        "calendar_end": end.isoformat(),
        "signal_sessions": [item.isoformat() for item in sessions],
        "market_bars_imported": imported,
    }
    record = write_run_record("database_init", summary)
    print(json.dumps({**summary, "run_record": str(record)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
