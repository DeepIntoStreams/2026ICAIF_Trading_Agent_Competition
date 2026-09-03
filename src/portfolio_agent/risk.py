"""Target-weight validation and deterministic repair."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping


# Absolute tolerance on the gross-exposure check. Without it, a weight vector that
# sums to exactly 1.0 in exact arithmetic (e.g. 0.2+0.2+0.1+0.15+0.15) evaluates to
# 1.0000000000000002 in float64 and trips a spurious "gross_exposure" violation.
GROSS_EPSILON = 1e-9


def sanitize_target_weights(
    raw_weights: Mapping[str, object],
    allowed_assets: Iterable[str],
    max_asset_weight: float = 0.10,
    max_gross_exposure: float = 1.00,
) -> tuple[dict[str, float], list[str]]:
    allowed = set(allowed_assets)
    cleaned: dict[str, float] = {}
    violations: list[str] = []

    for asset_id, raw_value in raw_weights.items():
        if asset_id not in allowed:
            violations.append("unknown_asset")
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            violations.append("invalid_number")
            value = 0.0
        if not math.isfinite(value):
            violations.append("invalid_number")
            value = 0.0
        if value < 0:
            violations.append("short_position")
            value = 0.0
        if value > max_asset_weight:
            violations.append("asset_cap")
            value = max_asset_weight
        cleaned[asset_id] = value

    total = sum(cleaned.values())
    if total > max_gross_exposure + GROSS_EPSILON:
        violations.append("gross_exposure")
        scale = max_gross_exposure / total
        cleaned = {asset: value * scale for asset, value in cleaned.items()}

    return cleaned, violations


def validate_target_weights(
    raw_weights: Mapping[str, object],
    allowed_assets: Iterable[str],
    max_asset_weight: float = 0.30,
    max_gross_exposure: float = 1.00,
) -> tuple[dict[str, float] | None, list[str]]:
    """Validate a submitted decision WITHOUT repairing it (reject-not-repair).

    Returns (weights, violations):
      * violations empty  -> the decision is VALID; `weights` are the parsed floats to execute.
      * violations present -> the decision is INVALID and must be REJECTED; `weights` is None
        and the caller keeps the previous portfolio (no trade). Invalid conditions: unknown
        ticker, non-numeric/non-finite weight, negative weight, any weight above the per-asset
        cap, or a total weight above the gross cap (both with a small float tolerance).
    """
    allowed = set(allowed_assets)
    weights: dict[str, float] = {}
    violations: list[str] = []

    for asset_id, raw_value in raw_weights.items():
        if asset_id not in allowed:
            violations.append("unknown_asset")
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            violations.append("invalid_number")
            continue
        if not math.isfinite(value):
            violations.append("invalid_number")
            continue
        if value < -GROSS_EPSILON:
            violations.append("short_position")
        if value > max_asset_weight + GROSS_EPSILON:
            violations.append("asset_cap")
        weights[asset_id] = value

    if sum(weights.values()) > max_gross_exposure + GROSS_EPSILON:
        violations.append("gross_exposure")

    if violations:
        return None, sorted(set(violations))
    return weights, []

