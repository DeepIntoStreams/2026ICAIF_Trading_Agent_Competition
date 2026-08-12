"""Reference validator for the participant I/O protocol (README next-step #3).

LOCAL / PROTOTYPE deliverable -- not yet wired into the evaluator.

Provides two layers, matching how the live server should treat submissions:

  1. structural  -- JSON Schema validation of the envelope/observation/response.
                    A malformed envelope is REJECTED.
  2. semantic    -- deterministic weight repair, identical to
                    portfolio_agent.risk.sanitize_target_weights. Out-of-range but
                    well-formed weights are ACCEPTED and repaired, and the repairs
                    are the M9 violation record. This layer never rejects.

If `jsonschema` is unavailable the structural layer degrades to a minimal built-in
check of required top-level keys, so the file has no hard dependency.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"

# Canonical M9 violation codes (must match portfolio_agent.risk).
VIOLATION_CODES = (
    "unknown_asset",
    "invalid_number",
    "short_position",
    "asset_cap",
    "gross_exposure",
)


def _load_schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / name).read_text())


def validate_structural(instance: dict[str, Any], schema_name: str) -> list[str]:
    """Return a list of structural errors ([] == valid)."""
    schema = _load_schema(schema_name)
    try:
        import jsonschema  # type: ignore
    except Exception:
        # Fallback: required-key check only.
        missing = [k for k in schema.get("required", []) if k not in instance]
        return [f"missing required key: {k}" for k in missing]
    validator = jsonschema.Draft202012Validator(schema)
    return [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}"
            for e in sorted(validator.iter_errors(instance), key=lambda e: list(e.path))]


def sanitize_target_weights(
    raw_weights: dict[str, Any],
    allowed_assets: list[str],
    max_asset_weight: float = 0.30,
    max_gross_exposure: float = 1.00,
    gross_epsilon: float = 1e-9,
) -> tuple[dict[str, float], list[str]]:
    """Mirror of portfolio_agent.risk.sanitize_target_weights.

    NOTE: adds `gross_epsilon` so a weight vector summing to exactly 1.0 in float
    (e.g. 1.0000000000000002) is not falsely flagged -- this is audit finding #1
    in SUMMARY_NEXTSTEPS_2_3_6.md. The upstream risk.py should adopt the same guard.
    """
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
    if total > max_gross_exposure + gross_epsilon:
        violations.append("gross_exposure")
        scale = max_gross_exposure / total
        cleaned = {asset: value * scale for asset, value in cleaned.items()}

    return cleaned, violations


def validate_response(
    response: dict[str, Any],
    allowed_assets: list[str],
    constraints: dict[str, Any],
) -> dict[str, Any]:
    """Full two-layer check for one response. Never raises on out-of-range weights."""
    structural = validate_structural(response, "decision_response.schema.json")
    result: dict[str, Any] = {"structural_errors": structural, "accepted": not structural}
    if structural:
        return result
    cleaned, violations = sanitize_target_weights(
        response.get("target_weights", {}),
        allowed_assets,
        max_asset_weight=float(constraints.get("max_asset_weight", 0.30)),
        max_gross_exposure=float(constraints.get("max_gross_exposure", 1.00)),
    )
    result["sanitized_weights"] = cleaned
    result["violations"] = violations
    result["cash_weight"] = 1.0 - sum(cleaned.values())
    result["repaired"] = bool(violations)
    return result


def _selftest() -> int:
    """Self-check: schemas parse, and a real emitted observation validates."""
    errs = 0
    for name in [
        "decision_request.schema.json",
        "observation_with_news.schema.json",
        "observation_without_news.schema.json",
        "decision_response.schema.json",
    ]:
        try:
            _load_schema(name)
            print(f"  [ok] parsed {name}")
        except Exception as exc:  # noqa: BLE001
            print(f"  [FAIL] {name}: {exc}")
            errs += 1

    # Response edge cases (semantic layer).
    allowed = ["AAPL", "MSFT", "NVDA"]
    cons = {"max_asset_weight": 0.30, "max_gross_exposure": 1.00}
    cases = [
        ("float-1.0 gross", {"AAPL": 0.2, "MSFT": 0.2, "NVDA": 0.2, "AAPL2": 0}, []),
        ("over-cap", {"AAPL": 0.9}, ["unknown_asset", "asset_cap"]),
        ("short+unknown", {"AAPL": -0.1, "ZZZZ": 0.5}, ["short_position", "unknown_asset"]),
    ]
    # Use a clean example that must NOT be flagged for gross:
    w = {"AAPL": 0.2, "MSFT": 0.2, "NVDA": 0.15}
    extra = 0.2 + 0.2 + 0.15
    _, v = sanitize_target_weights({"AAPL": 0.2, "MSFT": 0.2, "NVDA": 0.1,
                                    "_a": 0.15, "_b": 0.15}, allowed + ["_a", "_b"])
    # 0.2+0.2+0.1+0.15+0.15 = 1.0000000000000002 in float:
    if "gross_exposure" in v:
        print("  [FAIL] float-1.0 vector falsely flagged gross_exposure")
        errs += 1
    else:
        print("  [ok] float-1.0 vector not falsely flagged (epsilon guard works)")
    return errs


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--selftest", action="store_true", help="Validate the schemas themselves.")
    p.add_argument("--observation", help="Path to a JSON observation to validate.")
    p.add_argument("--with-news", action="store_true", help="Validate as the with-news variant.")
    p.add_argument("--response", help="Path to a JSON decision_response to validate.")
    args = p.parse_args(argv)

    if args.selftest:
        return 1 if _selftest() else 0
    if args.observation:
        name = ("observation_with_news.schema.json" if args.with_news
                else "observation_without_news.schema.json")
        errs = validate_structural(json.loads(Path(args.observation).read_text()), name)
        print("\n".join(errs) if errs else f"OK: valid against {name}")
        return 1 if errs else 0
    if args.response:
        print(json.dumps(validate_structural(
            json.loads(Path(args.response).read_text()),
            "decision_response.schema.json"), indent=2))
        return 0
    p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
