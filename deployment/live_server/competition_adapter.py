"""Thin boundary between Deployment data and the teammate-owned trading core."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from competition.code.engine import advance_day, validate_decision
from competition.code.trading_repo import TradingRepository


@dataclass(frozen=True)
class TeamDayInput:
    team_id: int
    submission_id: int
    execution_day_id: int
    prior_close_snapshot_id: int
    instrument_ids: list[int]
    ticker_by_id: dict[int, str]
    target_weights: dict[int, Decimal] | None
    open_prices: dict[int, Decimal]
    close_prices: dict[int, Decimal]
    constraints: dict[str, Any]
    initial_capital: Decimal
    submitted: bool
    observation_panel: dict[str, Any]


@dataclass(frozen=True)
class TeamDayOutput:
    record: dict[str, Any]
    observation: dict[str, Any]
    persistence_ids: dict[str, int]


class CompetitionAdapter:
    """Translate platform values, then delegate calculation and persistence."""

    @staticmethod
    def validate(raw_weights: dict[str, Any], tickers: list[str],
                 constraints: dict[str, Any]) -> dict[str, Any]:
        return validate_decision(raw_weights, tickers, constraints)

    @staticmethod
    def execute(connection: Any, value: TeamDayInput) -> TeamDayOutput:
        repository = TradingRepository(connection)
        prior = CompetitionAdapter._load_prior_state(
            connection,
            team_id=value.team_id,
            execution_day_id=value.execution_day_id,
            prior_close_snapshot_id=value.prior_close_snapshot_id,
        )
        cycle = advance_day(
            prior["state"], value.target_weights,
            value.open_prices, value.close_prices,
            value.instrument_ids, value.constraints,
            observation_panel=value.observation_panel,
            fee_rate=value.constraints.get("fee_rate", "0.001"),
            submitted=value.submitted,
            prev_close_nav=prior["prev_close_nav"],
            initial_capital=value.initial_capital,
            pre_validated=True,
        )
        ids = repository.persist_day(
            team_id=value.team_id,
            execution_day_id=value.execution_day_id,
            submission_id=value.submission_id,
            record=cycle["record"],
            initial_capital=value.initial_capital,
        )
        linked = connection.execute(
            """SELECT count(*) FROM portfolio_snapshots
                WHERE execution_id=%s
                  AND snapshot_type IN ('POST_OPEN','CLOSE')
                  AND prior_close_snapshot_id=%s""",
            (ids["execution_id"], value.prior_close_snapshot_id),
        ).fetchone()[0]
        if linked != 2:
            raise RuntimeError(
                f"execution {ids['execution_id']} did not persist two snapshots "
                f"linked to prior {value.prior_close_snapshot_id}"
            )
        observation = dict(cycle["next_observation"] or {})
        portfolio = dict(observation.get("portfolio") or {})
        portfolio["weights"] = {
            value.ticker_by_id[int(instrument_id)]: weight
            for instrument_id, weight in portfolio.get("weights", {}).items()
        }
        observation["portfolio"] = portfolio
        return TeamDayOutput(cycle["record"], observation, ids)

    @staticmethod
    def _load_prior_state(
        connection: Any,
        *,
        team_id: int,
        execution_day_id: int,
        prior_close_snapshot_id: int,
    ) -> dict[str, Any]:
        """Rebuild engine state from the observation-selected snapshot, never latest state."""
        row = connection.execute(
            """SELECT prior.id, prior.cash, prior.nav, prior.snapshot_type,
                      prior_day.trading_date, execution_day.trading_date
                 FROM portfolio_snapshots prior
                 LEFT JOIN trading_days prior_day ON prior_day.id=prior.trading_day_id
                 JOIN trading_days execution_day ON execution_day.id=%s
                WHERE prior.id=%s AND prior.team_id=%s""",
            (execution_day_id, prior_close_snapshot_id, team_id),
        ).fetchone()
        if row is None:
            raise RuntimeError(
                f"prior snapshot {prior_close_snapshot_id} does not belong to team {team_id}"
            )
        snapshot_id, cash, nav, snapshot_type, prior_date, execution_date = row
        if snapshot_type not in {"INITIAL", "CLOSE"}:
            raise RuntimeError(
                f"prior snapshot {snapshot_id} has invalid type {snapshot_type}"
            )
        if prior_date is None or prior_date >= execution_date:
            raise RuntimeError(
                f"prior snapshot {snapshot_id} is not before execution day {execution_date}"
            )

        positions = connection.execute(
            """SELECT instrument_id, quantity FROM position_snapshots
                WHERE portfolio_snapshot_id=%s""",
            (snapshot_id,),
        ).fetchall()
        shares = {int(instrument_id): quantity for instrument_id, quantity in positions}
        peak_nav = connection.execute(
            """WITH RECURSIVE ancestors AS (
                   SELECT id, nav, prior_close_snapshot_id
                     FROM portfolio_snapshots WHERE id=%s
                   UNION
                   SELECT predecessor.id, predecessor.nav,
                          predecessor.prior_close_snapshot_id
                     FROM portfolio_snapshots predecessor
                     JOIN ancestors child
                       ON predecessor.id=child.prior_close_snapshot_id
               ) SELECT max(nav) FROM ancestors""",
            (snapshot_id,),
        ).fetchone()[0]
        return {
            "state": {"cash": cash, "shares": shares, "peak_nav": peak_nav or nav},
            "prev_close_nav": nav,
            "prior_snapshot_id": int(snapshot_id),
        }
