import math

from portfolio_agent.execution import ExecutionEngine, PortfolioState


def test_close_execution_rebalances_to_target_weights_with_fees():
    engine = ExecutionEngine(fee_rate=0.001, slippage_bps=0.0)
    state = PortfolioState(
        cash=1_000_000.0,
        shares={"AAPL": 0.0, "MSFT": 0.0},
    )
    new_state, trades = engine.execute_close(
        state,
        target_weights={"AAPL": 0.5, "MSFT": 0.25},
        close_prices={"AAPL": 100.0, "MSFT": 50.0},
    )
    assert len(trades) == 2
    assert math.isclose(sum(t.trade_value for t in trades), 750_000.0)
    assert math.isclose(sum(t.fee for t in trades), 750.0)
    assert new_state.cash < 250_000.0
