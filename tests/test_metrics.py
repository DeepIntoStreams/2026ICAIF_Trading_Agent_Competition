import math
import unittest

from portfolio_agent.metrics import compute_metrics


class MetricTests(unittest.TestCase):
    def test_all_cash_metrics_are_zero(self):
        metrics = compute_metrics([1_000_000.0] * 5)
        self.assertEqual(metrics["total_return"], 0.0)
        self.assertEqual(metrics["sharpe"], 0.0)
        self.assertEqual(metrics["max_drawdown"], 0.0)

    def test_cost_turnover_and_violation_metrics(self):
        metrics = compute_metrics(
            [1_000_000.0, 1_010_000.0, 1_005_000.0],
            total_transaction_cost=1_000.0,
            total_trade_value=500_000.0,
            violation_steps=1,
            decision_steps=2,
        )
        self.assertTrue(math.isclose(metrics["cost_rate"], 0.001))
        self.assertTrue(math.isclose(metrics["violation_rate"], 0.5))
        self.assertLess(metrics["max_drawdown"], 0)


if __name__ == "__main__":
    unittest.main()
