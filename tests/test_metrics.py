import math

from portfolio_agent.metrics import compute_metrics


def test_metrics_report_m1_to_m9():
    metrics = compute_metrics(
        nav=[100.0, 110.0, 105.0, 120.0],
        total_transaction_cost=1.0,
        total_trade_value=50.0,
        violation_steps=1,
        decision_steps=3,
        annualization=252,
        risk_free_rate=0.0,
    )
    average_nav = (100.0 + 110.0 + 105.0 + 120.0) / 4
    assert math.isclose(metrics["m1_cumulative_return"], 0.20)
    assert math.isclose(metrics["m2_daily_win_rate"], 2 / 3)
    assert metrics["m3_sharpe_ratio"] is not None
    assert metrics["m4_sortino_ratio"] is not None
    assert metrics["m5_maximum_drawdown"] > 0
    assert metrics["m6_value_at_risk_95"] >= 0
    assert metrics["m7_expected_shortfall_95"] >= 0
    assert math.isclose(metrics["m8_turnover"], 50.0 / average_nav)
    assert math.isclose(metrics["m8_cost_rate"], 1.0 / average_nav)
    assert math.isclose(metrics["m9_violation_rate"], 1 / 3)
    assert metrics["sample_size"] == 3
    assert metrics["low_sample_warning"] is True


def test_zero_volatility_risk_metrics_are_null():
    metrics = compute_metrics([100.0, 100.0, 100.0], decision_steps=2)
    assert metrics["m1_cumulative_return"] == 0.0
    assert metrics["m2_daily_win_rate"] == 0.0
    assert metrics["m3_sharpe_ratio"] is None
    assert metrics["m4_sortino_ratio"] is None
    assert metrics["m5_maximum_drawdown"] == 0.0
