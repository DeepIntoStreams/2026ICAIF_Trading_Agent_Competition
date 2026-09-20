"""Platform orchestration for one atomic after-close Competition batch."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from deployment.bootstrap.common import DEFAULT_CONFIG, load_config

from .competition_adapter import CompetitionAdapter, TeamDayInput
from .observation_service import ObservationService
from .submission_service import SubmissionService


class DailyCompetitionService:
    """Own platform state while delegating trading math and persistence."""

    def __init__(self, config_path: Path = DEFAULT_CONFIG):
        self.config_path = config_path
        self.adapter = CompetitionAdapter()
        self.submissions = SubmissionService(self.adapter)
        self.observations = ObservationService()

    def process_imported_day(self, database_url: str, trading_date: date) -> dict[str, Any]:
        config = load_config(self.config_path)
        try:
            with psycopg.connect(database_url) as connection:
                return self._process(connection, trading_date, config)
        except Exception as exc:
            self._mark_failed(database_url, trading_date, exc)
            raise

    def _process(self, connection: Any, trading_date: date,
                 config: dict[str, Any]) -> dict[str, Any]:
        day = connection.execute(
            """SELECT id, market_open_at, market_close_at, submission_deadline_at,
                      market_status, execution_status, valuation_status, observation_status
                 FROM trading_days WHERE trading_date=%s FOR UPDATE""",
            (trading_date,),
        ).fetchone()
        if day is None:
            raise RuntimeError(f"trading day {trading_date} is not provisioned")
        day_id = int(day[0])
        market_open_at, market_close_at, deadline = day[1], day[2], day[3]
        if day[4] != "DATA_IMPORTED":
            raise RuntimeError(f"market data for {trading_date} is not complete")
        if day[5:] == ("COMPLETED", "COMPLETED", "PUBLISHED"):
            return self._completed_summary(connection, day_id, trading_date)
        if market_open_at is None or market_close_at is None or deadline is None:
            raise RuntimeError(f"market times for {trading_date} are not configured")

        signal = connection.execute(
            """SELECT id, trading_date FROM trading_days
                WHERE trading_date<%s ORDER BY trading_date DESC LIMIT 1""",
            (trading_date,),
        ).fetchone()
        if signal is None:
            raise RuntimeError(f"no signal day exists before {trading_date}")
        signal_day_id, signal_date = int(signal[0]), signal[1]

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
        configured_tickers = {item["ticker"] for item in config["instruments"]}
        imported_tickers = {str(row[1]) for row in instruments}
        if imported_tickers != configured_tickers:
            missing = sorted(configured_tickers - imported_tickers)
            unexpected = sorted(imported_tickers - configured_tickers)
            raise RuntimeError(
                f"market universe mismatch; missing={missing}, unexpected={unexpected}"
            )

        teams = connection.execute(
            "SELECT id, team_code FROM teams WHERE status='ACTIVE' ORDER BY id"
        ).fetchall()
        if not teams:
            raise RuntimeError("no active competition teams are configured")

        constraints = dict(config["constraints"])
        initial_capital = Decimal(str(config["initial_capital_usd"]))
        panel = self.observations.shared_panel(
            connection, instruments, trading_date, deadline, constraints,
        )
        instrument_ids = [int(row[0]) for row in instruments]
        ticker_by_id = {int(row[0]): str(row[1]) for row in instruments}
        open_prices = {int(row[0]): row[8] for row in instruments}
        close_prices = {int(row[0]): row[9] for row in instruments}
        tradable_instrument_ids = frozenset(
            int(row[0]) for row in instruments if bool(row[11])
        )

        self._set_processing(connection, day_id)
        processed_at = _now()
        counts = {"teams": 0, "executed": 0, "rejected": 0, "fallback": 0}
        for raw_team_id, raw_team_code in teams:
            team_id, team_code = int(raw_team_id), str(raw_team_code)
            prior_observation = connection.execute(
                """SELECT id, payload_json, close_portfolio_snapshot_id FROM observations
                    WHERE team_id=%s AND trading_day_id=%s AND published_at IS NOT NULL""",
                (team_id, signal_day_id),
            ).fetchone()
            if prior_observation is None:
                raise RuntimeError(
                    f"team {team_code} has no published observation for {signal_date}"
                )
            observation_id, prior_payload = int(prior_observation[0]), prior_observation[1]
            prior_close_snapshot_id = int(prior_observation[2])
            frozen_constraints = dict(prior_payload.get("constraints") or constraints)
            prepared = self.submissions.prepare(
                connection, team_id=team_id, team_code=team_code,
                observation_id=observation_id, signal_day_id=signal_day_id,
                signal_date=signal_date, execution_day_id=day_id,
                instruments=instruments, constraints=frozen_constraints,
            )
            output = self.adapter.execute(connection, TeamDayInput(
                team_id=team_id,
                submission_id=prepared.submission_id,
                execution_day_id=day_id,
                prior_close_snapshot_id=prior_close_snapshot_id,
                market_open_at=market_open_at,
                market_close_at=market_close_at,
                processed_at=processed_at,
                instrument_ids=instrument_ids,
                ticker_by_id=ticker_by_id,
                target_weights=prepared.target_weights,
                open_prices=open_prices,
                close_prices=close_prices,
                tradable_instrument_ids=tradable_instrument_ids,
                constraints=frozen_constraints,
                initial_capital=initial_capital,
                submitted=prepared.submitted,
                observation_panel=panel,
            ))
            self._verify_accounting(connection, output.persistence_ids, output.record)
            observation_id = self.observations.publish(
                connection, team_id=team_id, trading_day_id=day_id,
                close_snapshot_id=output.persistence_ids["close_snapshot_id"],
                payload=output.observation,
            )
            if prepared.status == "QUEUED":
                connection.execute(
                    "UPDATE decision_submissions SET status='EXECUTED', updated_at=%s WHERE id=%s",
                    (_now(), prepared.submission_id),
                )
            self._audit(connection, team_id, day_id, "EXECUTION_COMPLETED",
                        "execution", output.persistence_ids["execution_id"],
                        {"submission_id": prepared.submission_id,
                         "source": prepared.source,
                         "non_tradable_tickers": [
                             ticker_by_id[instrument_id]
                             for instrument_id in instrument_ids
                             if instrument_id not in tradable_instrument_ids
                         ]})
            self._audit(connection, team_id, day_id, "OBSERVATION_PUBLISHED",
                        "observation", observation_id, {})
            counts["teams"] += 1
            counts["executed"] += int(output.record["executed"])
            counts["rejected"] += int(prepared.status == "REJECTED")
            counts["fallback"] += int(prepared.source == "FALLBACK")

        leaderboard = self.adapter.compute_leaderboard(
            connection,
            trading_day_id=day_id,
            initial_capital=initial_capital,
            calculated_at=processed_at,
        )
        connection.execute(
            """UPDATE trading_days
                  SET execution_status='COMPLETED', valuation_status='COMPLETED',
                      observation_status='PUBLISHED', updated_at=%s
                WHERE id=%s""",
            (_now(), day_id),
        )
        counts["leaderboard_entries"] = len(leaderboard)
        self._audit(connection, None, day_id, "DAILY_LIVE_COMPLETED",
                    "trading_day", day_id, counts)
        return {
            "state": "completed",
            "trading_date": trading_date.isoformat(),
            "signal_date": signal_date.isoformat(),
            "teams_processed": counts["teams"],
            "decisions_executed": counts["executed"],
            "decisions_rejected": counts["rejected"],
            "fallback_decisions": counts["fallback"],
            "observations_published": counts["teams"],
            "leaderboard_entries": counts["leaderboard_entries"],
        }

    @staticmethod
    def _set_processing(connection: Any, day_id: int) -> None:
        connection.execute(
            """UPDATE trading_days
                  SET execution_status='PROCESSING', valuation_status='PROCESSING',
                      observation_status='GENERATING', updated_at=%s
                WHERE id=%s""",
            (_now(), day_id),
        )

    @staticmethod
    def _verify_accounting(connection: Any, ids: dict[str, int],
                           record: dict[str, Any]) -> None:
        cash, positions_value, nav = connection.execute(
            "SELECT cash, positions_value, nav FROM portfolio_snapshots WHERE id=%s",
            (ids["close_snapshot_id"],),
        ).fetchone()
        if cash + positions_value != nav or nav != record["nav_close"]:
            raise RuntimeError(f"CLOSE reconciliation failed for execution {ids['execution_id']}")
        performance_nav = connection.execute(
            "SELECT current_close_nav FROM daily_performance WHERE close_portfolio_snapshot_id=%s",
            (ids["close_snapshot_id"],),
        ).fetchone()[0]
        if performance_nav != nav:
            raise RuntimeError(
                f"performance reconciliation failed for execution {ids['execution_id']}"
            )

    @staticmethod
    def _completed_summary(connection: Any, day_id: int,
                           trading_date: date) -> dict[str, Any]:
        executions = connection.execute(
            "SELECT count(*) FROM executions WHERE trading_day_id=%s AND status='COMPLETED'",
            (day_id,),
        ).fetchone()[0]
        observations = connection.execute(
            "SELECT count(*) FROM observations WHERE trading_day_id=%s AND published_at IS NOT NULL",
            (day_id,),
        ).fetchone()[0]
        leaderboard_entries = connection.execute(
            "SELECT count(*) FROM leaderboard WHERE trading_day_id=%s",
            (day_id,),
        ).fetchone()[0]
        return {"state": "already_completed", "trading_date": trading_date.isoformat(),
                "teams_processed": int(executions),
                "observations_published": int(observations),
                "leaderboard_entries": int(leaderboard_entries)}

    @staticmethod
    def _audit(connection: Any, team_id: int | None, day_id: int,
               event_type: str, entity_type: str, entity_id: int,
               details: dict[str, Any]) -> None:
        connection.execute(
            """INSERT INTO audit_logs
                   (team_id, trading_day_id, actor_type, actor_id, event_type,
                    entity_type, entity_id, details_json, created_at)
               VALUES (%s,%s,'SYSTEM','daily_live',%s,%s,%s,%s,%s)""",
            (team_id, day_id, event_type, entity_type, entity_id,
             Jsonb(details), _now()),
        )

    @staticmethod
    def _mark_failed(database_url: str, trading_date: date, exc: Exception) -> None:
        try:
            with psycopg.connect(database_url) as connection:
                row = connection.execute(
                    "SELECT id FROM trading_days WHERE trading_date=%s FOR UPDATE",
                    (trading_date,),
                ).fetchone()
                if row is None:
                    return
                day_id = int(row[0])
                connection.execute(
                    """UPDATE trading_days
                          SET execution_status='FAILED', valuation_status='FAILED',
                              observation_status='FAILED', updated_at=%s
                        WHERE id=%s AND observation_status<>'PUBLISHED'""",
                    (_now(), day_id),
                )
                DailyCompetitionService._audit(
                    connection, None, day_id, "DAILY_LIVE_FAILED", "trading_day",
                    day_id, {"error_type": type(exc).__name__, "error": str(exc)[:1000]},
                )
        except psycopg.Error:
            pass


def _now() -> datetime:
    return datetime.now(timezone.utc)
