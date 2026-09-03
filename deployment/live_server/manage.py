"""Organizer CLI for PostgreSQL team and trading-calendar administration."""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime

from .store import CompetitionStore


def aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.environ.get("COMPETITION_DATABASE_URL"))
    commands = parser.add_subparsers(dest="command", required=True)
    team = commands.add_parser("register-team")
    team.add_argument("team_code")
    team.add_argument("--display-name")
    day = commands.add_parser("create-trading-day")
    day.add_argument("trading_date", type=date.fromisoformat)
    day.add_argument("--market-open-at", required=True, type=aware_datetime)
    day.add_argument("--market-close-at", required=True, type=aware_datetime)
    day.add_argument("--submission-open-at", required=True, type=aware_datetime)
    day.add_argument("--submission-deadline-at", required=True, type=aware_datetime)
    commands.add_parser("health")
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("COMPETITION_DATABASE_URL is required")
    store = CompetitionStore(args.database_url)
    if args.command == "register-team":
        key = store.register_team(args.team_code, args.display_name)
        print(json.dumps({"team_code": args.team_code, "api_key": key}))
    elif args.command == "create-trading-day":
        row, idempotent = store.create_trading_day(
            args.trading_date, args.market_open_at, args.market_close_at,
            args.submission_open_at, args.submission_deadline_at,
        )
        print(json.dumps({"id": row["id"], "trading_date": str(row["trading_date"]),
                          "idempotent": idempotent}))
    else:
        healthy = store.health()
        print(json.dumps({"database": "ok" if healthy else "unavailable"}))
        return 0 if healthy else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
