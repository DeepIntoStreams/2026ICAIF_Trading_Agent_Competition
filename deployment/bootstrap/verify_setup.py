#!/usr/bin/env python3
"""Verify core database/bootstrap invariants and print a compact JSON report."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import psycopg

from deployment.bootstrap.common import DEFAULT_CONFIG, load_config, safe_database_target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("COMPETITION_DATABASE_URL"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("COMPETITION_DATABASE_URL is required")
    config = load_config(args.config)
    with psycopg.connect(args.database_url) as connection:
        instrument_count = connection.execute(
            "SELECT count(*) FROM instruments WHERE is_active"
        ).fetchone()[0]
        days = connection.execute(
            """SELECT td.trading_date, td.market_status, count(mb.id) AS bars
                 FROM trading_days td
                 LEFT JOIN market_bars mb ON mb.trading_day_id=td.id
                GROUP BY td.id ORDER BY td.trading_date"""
        ).fetchall()

    expected_instruments = len(config["instruments"])
    checks = {
        "stock_universe_complete": instrument_count == expected_instruments,
        "trading_calendar_provisioned": bool(days),
        "imported_days_complete": all(
            status != "DATA_IMPORTED" or bars == expected_instruments
            for _, status, bars in days
        ),
    }
    report = {
        "database": safe_database_target(args.database_url),
        "checks": checks,
        "counts": {
            "active_instruments": instrument_count,
            "trading_days": len(days),
        },
        "trading_days": [
            {"date": str(day), "market_status": status, "bars": bars}
            for day, status, bars in days
        ],
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
