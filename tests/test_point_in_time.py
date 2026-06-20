import unittest

import pandas as pd

from portfolio_agent.point_in_time import latest_available_fundamentals


class PointInTimeTests(unittest.TestCase):
    def test_unreleased_report_is_not_visible(self):
        frame = pd.DataFrame(
            [
                {"ticker": "AAA", "period_end": "2025-03-31", "available_at": "2025-05-01T12:00:00Z", "net_income": 10},
                {"ticker": "AAA", "period_end": "2025-06-30", "available_at": "2025-08-01T12:00:00Z", "net_income": 20},
            ]
        )
        visible = latest_available_fundamentals(frame, pd.Timestamp("2025-07-01T00:00:00Z"))
        self.assertEqual(visible.iloc[0]["net_income"], 10)

    def test_later_restatement_is_not_visible_early(self):
        frame = pd.DataFrame(
            [
                {"ticker": "AAA", "period_end": "2025-03-31", "available_at": "2025-05-01T12:00:00Z", "net_income": 10},
                {"ticker": "AAA", "period_end": "2025-03-31", "available_at": "2025-09-01T12:00:00Z", "net_income": 8},
            ]
        )
        visible = latest_available_fundamentals(frame, pd.Timestamp("2025-06-01T00:00:00Z"))
        self.assertEqual(visible.iloc[0]["net_income"], 10)


if __name__ == "__main__":
    unittest.main()
