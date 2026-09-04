#!/usr/bin/env python3
"""Reset and seed a disposable PostgreSQL receiver test database."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from deployment.live_server.store import CompetitionStore


ROOT = Path(__file__).parents[2]
DATABASE_URL = os.environ.get("TEST_COMPETITION_DATABASE_URL", "")
API_KEY = "receiver-manual-test-api-key"


def require_test_database() -> None:
    if not DATABASE_URL:
        raise SystemExit("set TEST_COMPETITION_DATABASE_URL")
    with psycopg.connect(DATABASE_URL) as connection:
        name = connection.execute("SELECT current_database()").fetchone()[0]
    if not name.endswith("_test"):
        raise SystemExit(
            f"refusing to reset database {name!r}; its name must end with '_test'"
        )


def main() -> int:
    require_test_database()
    schema = (ROOT / "data" / "database" / "schema.sql").read_text(encoding="utf-8")
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(schema)
        connection.execute(
            """TRUNCATE audit_logs, daily_performance, cash_ledger, transactions,
               executions, submission_weights, decision_submissions, submission_attempts,
               observations, position_snapshots, portfolio_snapshots, fundamental_records,
               market_bars, trading_days, instruments, teams
               RESTART IDENTITY CASCADE"""
        )

    store = CompetitionStore(DATABASE_URL)
    store.register_team("manual_team", "Manual Test Team", API_KEY)

    now = datetime.now(timezone.utc)
    signal_date = now.date()
    next_date = signal_date + timedelta(days=1)
    submission_open = now - timedelta(hours=1)
    deadline = now + timedelta(hours=1)
    signal_open = datetime.combine(signal_date, time(13, 30), timezone.utc)
    signal_close = datetime.combine(signal_date, time(20), timezone.utc)
    next_open = datetime.combine(next_date, time(13, 30), timezone.utc)
    next_close = datetime.combine(next_date, time(20), timezone.utc)

    store.create_trading_day(
        signal_date, signal_open, signal_close, submission_open, deadline
    )
    store.create_trading_day(
        next_date, next_open, next_close, next_close,
        next_close + timedelta(hours=17, minutes=29),
    )

    payload = {
        "session_date": signal_date.isoformat(),
        "event_time_utc": deadline.isoformat(),
        "assets": [{"ticker": "AAPL"}, {"ticker": "MSFT"}],
        "market_features": {},
        "fundamental_features": {},
        "portfolio": {"weights": {}, "cash_ratio": 1.0, "nav": 1_000_000},
        "constraints": {
            "long_only": True,
            "max_asset_weight": 0.10,
            "max_gross_exposure": 1.0,
        },
        "news": [],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    with psycopg.connect(DATABASE_URL) as connection:
        team_id = connection.execute(
            "SELECT id FROM teams WHERE team_code='manual_team'"
        ).fetchone()[0]
        day_id = connection.execute(
            "SELECT id FROM trading_days WHERE trading_date=%s", (signal_date,)
        ).fetchone()[0]
        snapshot_id = connection.execute(
            """INSERT INTO portfolio_snapshots
                   (team_id, trading_day_id, snapshot_type, cash, positions_value,
                    nav, gross_exposure, drawdown, effective_at, created_at)
               VALUES (%s, %s, 'CLOSE', 1000000, 0, 1000000, 0, 0, %s, %s)
               RETURNING id""",
            (team_id, day_id, signal_close, now),
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO observations
                   (team_id, trading_day_id, close_portfolio_snapshot_id,
                    payload_json, payload_hash, generated_at, published_at, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                team_id,
                day_id,
                snapshot_id,
                Jsonb(payload),
                hashlib.sha256(canonical.encode()).hexdigest(),
                now,
                now,
                now,
            ),
        )

    print(json.dumps({
        "database": DATABASE_URL.rsplit("@", 1)[-1],
        "team_id": "manual_team",
        "api_key": API_KEY,
        "session_date": signal_date.isoformat(),
        "submission_open_at": submission_open.isoformat(),
        "submission_deadline_at": deadline.isoformat(),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
