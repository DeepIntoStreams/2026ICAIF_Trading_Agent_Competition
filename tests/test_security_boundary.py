import unittest

import numpy as np
import pandas as pd

from portfolio_agent.observation import build_observation, compute_market_features
from portfolio_agent.security import assert_agent_safe_observation, make_asset_id


class SecurityBoundaryTests(unittest.TestCase):
    def test_asset_id_is_stable_and_does_not_expose_ticker(self):
        asset_id = make_asset_id("TEST_ASSET", b"unit-test-secret")
        self.assertEqual(asset_id, make_asset_id("TEST_ASSET", b"unit-test-secret"))
        self.assertNotIn("TEST_ASSET", asset_id)

    def test_sensitive_fields_are_rejected(self):
        for field in ["ticker", "date", "close", "volume", "revenue", "raw_path"]:
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "Sensitive field"):
                assert_agent_safe_observation({"payload": {field: "secret"}})

    def test_built_observation_contains_only_derived_values(self):
        secret = b"unit-test-secret"
        aid = make_asset_id("TEST_ASSET", secret)

        closes = np.array([100.0, 101.0, 102.0])
        volumes = np.array([1000.0, 1100.0, 900.0])
        mf = {aid: compute_market_features(closes, volumes)}
        ff = {aid: {"net_margin": 0.2, "report_age_days": 20}}
        weights = {aid: 0.1}

        observation = build_observation(
            step_id=5,
            market_features=mf,
            fundamental_features=ff,
            portfolio_weights=weights,
            cash_ratio=0.9,
            nav_ratio=1.02,
            drawdown=-0.01,
            constraints={
                "long_only": True,
                "max_asset_weight": 0.30,
                "max_gross_exposure": 1.00,
                "fee_rate": 0.001,
            },
        )
        assert_agent_safe_observation(observation)
        serialized = str(observation)
        self.assertNotIn("TEST_ASSET", serialized)
        self.assertNotIn("1000.0", serialized)

    def test_different_secrets_produce_different_ids(self):
        id1 = make_asset_id("AAPL", b"secret-1")
        id2 = make_asset_id("AAPL", b"secret-2")
        self.assertNotEqual(id1, id2)


if __name__ == "__main__":
    unittest.main()
