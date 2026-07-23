"""Competition portfolio metrics with explicit edge-case handling."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np


def _nullable_float(value: float) -> float | None:
    if not math.isfinite(value):
        return None
    return float(value)


def compute_metrics(
    nav: Sequence[float],
    initial_capital: float | None = None,
    total_transaction_cost: float = 0.0,
    total_trade_value: float = 0.0,
    violation_steps: int = 0,
    decision_steps: int | None = None,
    annualization: int = 252,
    risk_free_rate: float = 0.0,
    var_confidence: float = 0.95,
) -> dict[str, float | int | bool | None]:
    values = np.asarray(nav, dtype=float)
    if len(values) < 2 or np.any(~np.isfinite(values)) or np.any(values <= 0):
        raise ValueError("NAV must contain at least two positive finite values")

    returns = values[1:] / values[:-1] - 1.0
    n = len(returns)
    decision_steps = n if decision_steps is None else int(decision_steps)
    rf_daily = float(risk_free_rate) / float(annualization)
    excess_returns = returns - rf_daily

    cumulative_return = float(values[-1] / values[0] - 1.0)
    daily_win_rate = float(np.mean(returns > 0.0)) if n > 0 else 0.0

    volatility = float(np.std(excess_returns, ddof=1)) if n > 1 else 0.0
    sharpe: float | None = None
    if volatility > 0:
        sharpe = _nullable_float(
            math.sqrt(annualization) * float(np.mean(excess_returns)) / volatility
        )

    downside = excess_returns[excess_returns < 0.0]
    sortino: float | None = None
    if len(downside) > 0:
        downside_deviation = float(np.sqrt(np.mean(downside ** 2)))
        if downside_deviation > 0:
            sortino = _nullable_float(
                math.sqrt(annualization)
                * float(np.mean(excess_returns))
                / downside_deviation
            )

    running_peak = np.maximum.accumulate(values)
    drawdowns = 1.0 - values / running_peak
    maximum_drawdown = float(np.max(drawdowns))

    quantile = float(np.quantile(returns, 1.0 - var_confidence))
    value_at_risk = float(max(0.0, -quantile))
    tail_losses = [-float(ret) for ret in returns if ret <= quantile]
    expected_shortfall = (
        float(np.mean(tail_losses)) if tail_losses else None
    )

    average_nav = float(np.mean(values))
    turnover = (
        float(total_trade_value / average_nav) if average_nav > 0 else 0.0
    )
    cost_rate = (
        float(total_transaction_cost / average_nav) if average_nav > 0 else 0.0
    )
    violation_rate = (
        0.0
        if decision_steps == 0
        else float(violation_steps / decision_steps)
    )

    result: dict[str, float | int | bool | None] = {
        "m1_cumulative_return": cumulative_return,
        "m2_daily_win_rate": daily_win_rate,
        "m3_sharpe_ratio": sharpe,
        "m4_sortino_ratio": sortino,
        "m5_maximum_drawdown": maximum_drawdown,
        "m6_value_at_risk_95": value_at_risk,
        "m7_expected_shortfall_95": expected_shortfall,
        "m8_turnover": turnover,
        "m8_cost_rate": cost_rate,
        "m9_violation_rate": violation_rate,
        "sample_size": n,
        "low_sample_warning": n < 30,
    }

    result.update(
        {
            "total_return": cumulative_return,
            "sharpe": sharpe,
            "sortino": sortino,
            "max_drawdown": maximum_drawdown,
            "turnover": turnover,
            "cost_rate": cost_rate,
            "violation_rate": violation_rate,
        }
    )
    return result
