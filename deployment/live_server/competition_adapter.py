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
        prior = repository.load_state(
            value.team_id, initial_capital=value.initial_capital,
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
        observation = dict(cycle["next_observation"] or {})
        portfolio = dict(observation.get("portfolio") or {})
        portfolio["weights"] = {
            value.ticker_by_id[int(instrument_id)]: weight
            for instrument_id, weight in portfolio.get("weights", {}).items()
        }
        observation["portfolio"] = portfolio
        return TeamDayOutput(cycle["record"], observation, ids)
