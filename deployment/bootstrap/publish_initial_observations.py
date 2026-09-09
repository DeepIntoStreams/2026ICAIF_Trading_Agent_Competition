#!/usr/bin/env python3
"""Publish the first team observations from funded INITIAL snapshots."""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from deployment.bootstrap.common import DEFAULT_CONFIG, load_config, safe_database_target
from deployment.live_server.calendar_service import require_daily_live_calendar
from deployment.live_server.observation_service import ObservationService
from deployment.live_server.store import CompetitionStore


def publish_initial_observations(
    database_url: str,
    signal_date: date,
    *,
    config_path: Path = DEFAULT_CONFIG,
) -> dict[str, object]:
    """Atomically publish one bootstrap observation for every ACTIVE team.

    The first observation is the one legitimate exception to the usual
    observation -> CLOSE-snapshot link: it references the team's funded
    INITIAL snapshot. Every later observation is produced by daily settlement
    and references that day's CLOSE snapshot.
    """
    config = load_config(config_path)
    store = CompetitionStore(database_url)
    following_date = require_daily_live_calendar(store, signal_date)

    with psycopg.connect(database_url) as connection:
        day = connection.execute(
            """SELECT id, market_close_at, submission_deadline_at, market_status,
                      execution_status, valuation_status, observation_status
                 FROM trading_days WHERE trading_date=%s FOR UPDATE""",
            (signal_date,),
        ).fetchone()
        if day is None:
            raise RuntimeError(f"trading day {signal_date} is not provisioned")
        day_id = int(day[0])
        if day[3] != "DATA_IMPORTED":
            raise RuntimeError(
                f"initial signal-day market data is not complete: {day[3]}"
            )
        execution_count = connection.execute(
            "SELECT count(*) FROM executions",
        ).fetchone()[0]
        if execution_count:
            raise RuntimeError(
                "initial observations cannot be published after executions exist"
            )

        instruments = connection.execute(
            """SELECT i.id, i.ticker, i.company_name, i.sector,
                      mb.open, mb.high, mb.low, mb.close,
                      mb.adjusted_open, mb.adjusted_close, mb.volume,
                      mb.is_tradable, mb.verification_status
                 FROM instruments i
                 JOIN market_bars mb ON mb.instrument_id=i.id
                WHERE i.is_active AND mb.trading_day_id=%s
                ORDER BY i.id""",
            (day_id,),
        ).fetchall()
        configured_tickers = {str(item["ticker"]) for item in config["instruments"]}
        imported_tickers = {str(row[1]) for row in instruments}
        if imported_tickers != configured_tickers:
            raise RuntimeError(
                "initial market universe mismatch; "
                f"missing={sorted(configured_tickers - imported_tickers)}, "
                f"unexpected={sorted(imported_tickers - configured_tickers)}"
            )

        teams = connection.execute(
            "SELECT id, team_code FROM teams WHERE status='ACTIVE' ORDER BY id",
        ).fetchall()
        if not teams:
            raise RuntimeError("no active competition teams are configured")

        other_observations = connection.execute(
            "SELECT count(*) FROM observations WHERE trading_day_id<>%s",
            (day_id,),
        ).fetchone()[0]
        if other_observations:
            raise RuntimeError(
                "initial observations cannot be published after another signal day"
            )

        panel = ObservationService.shared_panel(
            connection,
            instruments,
            signal_date,
            day[2],
            dict(config["constraints"]),
        )
        created = 0
        existing = 0
        timestamp = datetime.now(timezone.utc)
        for raw_team_id, raw_team_code in teams:
            team_id, team_code = int(raw_team_id), str(raw_team_code)
            snapshots = connection.execute(
                """SELECT id, cash, positions_value, nav
                     FROM portfolio_snapshots
                    WHERE team_id=%s AND snapshot_type='INITIAL'
                    ORDER BY id""",
                (team_id,),
            ).fetchall()
            if len(snapshots) != 1:
                raise RuntimeError(
                    f"team {team_code} must have exactly one INITIAL snapshot"
                )
            snapshot_id, cash, positions_value, nav = snapshots[0]
            if Decimal(nav) <= 0 or Decimal(cash) + Decimal(positions_value) != Decimal(nav):
                raise RuntimeError(f"team {team_code} INITIAL snapshot does not reconcile")

            positions = connection.execute(
                """SELECT i.ticker, pos.weight
                     FROM position_snapshots pos
                     JOIN instruments i ON i.id=pos.instrument_id
                    WHERE pos.portfolio_snapshot_id=%s ORDER BY i.id""",
                (snapshot_id,),
            ).fetchall()
            payload = {
                **panel,
                "portfolio": {
                    "weights": {str(ticker): float(weight) for ticker, weight in positions},
                    "cash_ratio": float(Decimal(cash) / Decimal(nav)),
                    "nav": float(nav),
                },
            }
            observation = connection.execute(
                """SELECT close_portfolio_snapshot_id, payload_json
                     FROM observations
                    WHERE team_id=%s AND trading_day_id=%s""",
                (team_id, day_id),
            ).fetchone()
            if observation is not None:
                if int(observation[0]) != int(snapshot_id):
                    raise RuntimeError(
                        f"team {team_code} initial observation references another snapshot"
                    )
                if observation[1] != payload:
                    raise RuntimeError(
                        f"team {team_code} initial observation differs from current inputs"
                    )
                existing += 1
                continue

            observation_id = ObservationService.publish(
                connection,
                team_id=team_id,
                trading_day_id=day_id,
                close_snapshot_id=int(snapshot_id),
                payload=payload,
            )
            connection.execute(
                """INSERT INTO audit_logs
                       (team_id, trading_day_id, actor_type, actor_id, event_type,
                        entity_type, entity_id, details_json, created_at)
                   VALUES (%s,%s,'ADMIN','bootstrap','INITIAL_OBSERVATION_PUBLISHED',
                           'observation',%s,%s,%s)""",
                (
                    team_id,
                    day_id,
                    observation_id,
                    Jsonb({"source_snapshot_type": "INITIAL"}),
                    timestamp,
                ),
            )
            created += 1

        connection.execute(
            """UPDATE trading_days
                  SET execution_status='COMPLETED', valuation_status='COMPLETED',
                      observation_status='PUBLISHED', updated_at=%s
                WHERE id=%s""",
            (timestamp, day_id),
        )

    return {
        "database": safe_database_target(database_url),
        "signal_date": signal_date.isoformat(),
        "execution_date": following_date.isoformat(),
        "active_teams": len(teams),
        "observations_created": created,
        "observations_existing": existing,
        "state": "initial_observations_published",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--database-url", default=os.environ.get("COMPETITION_DATABASE_URL"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or COMPETITION_DATABASE_URL is required")
    result = publish_initial_observations(
        args.database_url,
        args.date,
        config_path=args.config,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
