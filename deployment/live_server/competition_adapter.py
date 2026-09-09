"""Thin boundary between Deployment data and the teammate-owned trading core."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from competition.code.engine import advance_day, validate_decision
from competition.code.trading_repo import (
    TradingRepository,
    compute_and_store_leaderboard,
)


@dataclass(frozen=True)
class TeamDayInput:
    team_id: int
    submission_id: int
    execution_day_id: int
    prior_close_snapshot_id: int
    market_open_at: datetime
    market_close_at: datetime
    processed_at: datetime
    instrument_ids: list[int]
    ticker_by_id: dict[int, str]
    target_weights: dict[int, Decimal] | None
    open_prices: dict[int, Decimal]
    close_prices: dict[int, Decimal]
    tradable_instrument_ids: frozenset[int]
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
        prior = repository.load_state(
            value.team_id,
            prior_close_snapshot_id=value.prior_close_snapshot_id,
            execution_day_id=value.execution_day_id,
            initial_capital=value.initial_capital,
        )
        execution_weights = CompetitionAdapter._freeze_non_tradable_positions(
            value.target_weights,
            prior["state"],
            value.open_prices,
            value.instrument_ids,
            value.tradable_instrument_ids,
        )
        cycle = advance_day(
            prior["state"], execution_weights,
            value.open_prices, value.close_prices,
            value.instrument_ids, value.constraints,
            observation_panel=value.observation_panel,
            fee_rate=value.constraints.get("fee_rate", "0.001"),
            submitted=value.submitted,
            prev_close_nav=prior["prev_close_nav"],
            initial_capital=value.initial_capital,
            pre_validated=True,
        )
        cycle["record"]["non_tradable_instruments"] = [
            instrument_id for instrument_id in value.instrument_ids
            if instrument_id not in value.tradable_instrument_ids
        ]
        ids = repository.persist_day(
            team_id=value.team_id,
            execution_day_id=value.execution_day_id,
            submission_id=value.submission_id,
            record=cycle["record"],
            prior_close_snapshot_id=value.prior_close_snapshot_id,
            initial_capital=value.initial_capital,
            scheduled_at=value.market_open_at,
            open_effective_at=value.market_open_at,
            close_effective_at=value.market_close_at,
            processed_at=value.processed_at,
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
    def _freeze_non_tradable_positions(
        target_weights: dict[int, Decimal] | None,
        state: dict[str, Any],
        open_prices: dict[int, Decimal],
        instrument_ids: list[int],
        tradable_instrument_ids: frozenset[int],
    ) -> dict[int, Decimal] | None:
        """Translate a market halt into an executable target without core changes.

        The core accepts target weights, so Deployment replaces each halted
        instrument's requested target with the weight that reproduces its
        current share quantity at the carried trusted open price. The original
        participant vector remains unchanged in decision_submissions and
        submission_weights.
        """
        if target_weights is None:
            return None
        non_tradable = [
            instrument_id for instrument_id in instrument_ids
            if instrument_id not in tradable_instrument_ids
        ]
        if not non_tradable:
            return dict(target_weights)

        cash = Decimal(str(state["cash"]))
        shares = {
            int(instrument_id): Decimal(str(quantity))
            for instrument_id, quantity in (state.get("shares") or {}).items()
        }
        nav_open = cash + sum(
            (
                quantity * Decimal(str(open_prices[instrument_id]))
                for instrument_id, quantity in shares.items()
                if instrument_id in open_prices
            ),
            Decimal("0"),
        )
        if nav_open <= 0:
            raise RuntimeError("cannot freeze a non-tradable position with non-positive NAV")

        adjusted = dict(target_weights)
        for instrument_id in non_tradable:
            price = open_prices.get(instrument_id)
            if price is None or Decimal(str(price)) <= 0:
                raise RuntimeError(
                    f"non-tradable instrument {instrument_id} has no valuation price"
                )
            quantity = shares.get(instrument_id, Decimal("0"))
            adjusted[instrument_id] = quantity * Decimal(str(price)) / nav_open
        return adjusted

    @staticmethod
    def compute_leaderboard(
        connection: Any,
        *,
        trading_day_id: int,
        initial_capital: Decimal,
        calculated_at: datetime,
    ) -> list[dict[str, Any]]:
        """Delegate official ranking to the Competition-owned implementation."""
        return compute_and_store_leaderboard(
            connection,
            trading_day_id,
            initial_capital=initial_capital,
            now=calculated_at,
        )
