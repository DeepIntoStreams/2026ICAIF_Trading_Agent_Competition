"""Portfolio evaluation metrics with explicit edge-case handling."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np


def compute_metrics(
    nav: Sequence[float],
    total_transaction_cost: float = 0.0,
    total_trade_value: float = 0.0,
    violation_steps: int = 0,
    decision_steps: int | None = None,
    annualization: int = 252,
) -> dict[str, float | None]:
    values = np.asarray(nav, dtype=float)
    if len(values) < 2 or np.any(~np.isfinite(values)) or np.any(values <= 0):
        raise ValueError("NAV must contain at least two positive finite values")

    returns = values[1:] / values[:-1] - 1.0
    n = len(returns)
    total_return = values[-1] / values[0] - 1.0
    annualized_return = (values[-1] / values[0]) ** (annualization / n) - 1.0
    volatility = float(np.std(returns, ddof=1)) if n > 1 else 0.0
    annualized_volatility = math.sqrt(annualization) * volatility
    mean_return = float(np.mean(returns))

    sharpe = 0.0
    if volatility > 0:
        sharpe = math.sqrt(annualization) * mean_return / volatility

    downside = returns[returns < 0]
    if len(downside) > 1:
        downside_std = float(np.std(downside, ddof=1))
    else:
        downside_std = 0.0
    sortino = 0.0
    if downside_std > 0:
        sortino = math.sqrt(annualization) * mean_return / downside_std

    running_max = np.maximum.accumulate(values)
    drawdowns = values / running_max - 1.0
    max_drawdown = float(np.min(drawdowns))

    calmar: float | None = None
    if max_drawdown < 0:
        calmar = annualized_return / abs(max_drawdown)

    average_nav = float(np.mean(values))
    decision_steps = n if decision_steps is None else decision_steps

    return {
        "total_return": float(total_return),
        "annualized_return": float(annualized_return),
        "annualized_volatility": float(annualized_volatility),
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "max_drawdown": max_drawdown,
        "calmar": calmar,
        "cost_rate": float(total_transaction_cost / values[0]),
        "turnover": float(total_trade_value / average_nav) if average_nav > 0 else 0.0,
        "violation_rate": 0.0 if decision_steps == 0 else float(violation_steps / decision_steps),
    }
