"""M9 sanitization rules, including the gross-exposure epsilon regression (audit #1)."""

from portfolio_agent.risk import sanitize_target_weights, validate_target_weights


def test_unknown_asset_dropped():
    cleaned, v = sanitize_target_weights({"AAPL": 0.2, "ZZZZ": 0.3}, ["AAPL"])
    assert "unknown_asset" in v
    assert "ZZZZ" not in cleaned


def test_short_position_clipped():
    cleaned, v = sanitize_target_weights({"AAPL": -0.1}, ["AAPL"])
    assert "short_position" in v
    assert cleaned["AAPL"] == 0.0


def test_invalid_number():
    cleaned, v = sanitize_target_weights({"AAPL": "oops"}, ["AAPL"])
    assert "invalid_number" in v
    assert cleaned["AAPL"] == 0.0


def test_asset_cap_clipped():
    cleaned, v = sanitize_target_weights({"AAPL": 0.9}, ["AAPL"], max_asset_weight=0.30)
    assert "asset_cap" in v
    assert cleaned["AAPL"] == 0.30


def test_gross_exposure_scaled():
    cleaned, v = sanitize_target_weights(
        {"AAPL": 0.3, "MSFT": 0.3, "NVDA": 0.3, "GS": 0.3}, ["AAPL", "MSFT", "NVDA", "GS"],
        max_asset_weight=0.30, max_gross_exposure=1.00)
    assert "gross_exposure" in v
    assert abs(sum(cleaned.values()) - 1.00) < 1e-9


def test_float_one_not_flagged():
    """Regression for audit finding #1: a vector summing to 1.0 in exact arithmetic
    (1.0000000000000002 in float64) must NOT trip gross_exposure."""
    weights = {"GS": 0.2, "LLY": 0.2, "AAPL": 0.2, "UNH": 0.15, "NVDA": 0.15, "MSFT": 0.1}
    assert sum(weights.values()) > 1.0  # sums to 1.0000000000000002 in float64
    _, v = sanitize_target_weights(weights, list(weights), max_gross_exposure=1.00)
    assert "gross_exposure" not in v


# --- validate_target_weights: reject-not-repair (competition engine + web /submit) ---

UNIV = ["AAPL", "MSFT", "NVDA", "JPM", "GS"]


def test_validate_accepts_valid_decision():
    """A compliant decision is accepted unchanged (no repair)."""
    w = {"AAPL": 0.30, "MSFT": 0.20, "NVDA": 0.10}
    weights, viol = validate_target_weights(w, UNIV, max_asset_weight=0.30, max_gross_exposure=1.00)
    assert viol == []
    assert weights == w                       # returned verbatim, not clipped/scaled


def test_validate_rejects_over_cap():
    weights, viol = validate_target_weights({"AAPL": 0.5}, UNIV, 0.30, 1.00)
    assert weights is None and "asset_cap" in viol


def test_validate_rejects_gross_over_one():
    w = {"AAPL": 0.30, "MSFT": 0.30, "NVDA": 0.30, "JPM": 0.30}   # sum 1.2
    weights, viol = validate_target_weights(w, UNIV, 0.30, 1.00)
    assert weights is None and "gross_exposure" in viol


def test_validate_rejects_negative_unknown_and_malformed():
    assert validate_target_weights({"AAPL": -0.1}, UNIV, 0.30, 1.00)[1] == ["short_position"]
    assert validate_target_weights({"ZZZZ": 0.1}, UNIV, 0.30, 1.00)[1] == ["unknown_asset"]
    assert validate_target_weights({"AAPL": "oops"}, UNIV, 0.30, 1.00)[1] == ["invalid_number"]


def test_validate_float_tolerance_at_bounds():
    """A vector at exactly the cap and summing to 1.0 in float64 is NOT spuriously rejected."""
    w = {"AAPL": 0.30, "MSFT": 0.30, "NVDA": 0.30, "JPM": 0.10}   # sum 1.0 (float wobble)
    weights, viol = validate_target_weights(w, UNIV, 0.30, 1.00)
    assert viol == [] and weights == w
