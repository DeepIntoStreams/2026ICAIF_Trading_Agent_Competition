import math
import unittest

from portfolio_agent.risk import sanitize_target_weights


class RiskTests(unittest.TestCase):
    def test_sanitizer_repairs_invalid_action_and_records_violations(self):
        cleaned, violations = sanitize_target_weights(
            {"asset_a": 0.50, "asset_b": -0.10, "asset_c": float("nan"), "unknown": 0.20},
            allowed_assets={"asset_a", "asset_b", "asset_c"},
        )
        self.assertEqual(cleaned, {"asset_a": 0.30, "asset_b": 0.0, "asset_c": 0.0})
        self.assertEqual(set(violations), {"asset_cap", "short_position", "invalid_number", "unknown_asset"})

    def test_total_exposure_is_scaled_to_one(self):
        cleaned, violations = sanitize_target_weights(
            {"a": 0.30, "b": 0.30, "c": 0.30, "d": 0.30},
            allowed_assets={"a", "b", "c", "d"},
        )
        self.assertTrue(math.isclose(sum(cleaned.values()), 1.0))
        self.assertIn("gross_exposure", violations)


if __name__ == "__main__":
    unittest.main()
