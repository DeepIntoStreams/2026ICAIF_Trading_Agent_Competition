"""Protocol schema round-trip + no-leakage checks (README #3).

Skips schema tests gracefully if `jsonschema` is not installed.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    return json.loads((ROOT / "schemas" / name).read_text())


def test_all_schemas_parse():
    for name in ["decision_request.schema.json", "observation_with_news.schema.json",
                 "observation_without_news.schema.json", "decision_response.schema.json"]:
        _load(name)


def _sample_observation_with_news():
    return {
        "session_date": "2026-06-05",
        "event_time_utc": "2026-06-05T19:50:00+00:00",
        "assets": [{"ticker": "AAPL", "company_name": "Apple Inc.",
                    "sector": "technology", "open_price": 195.0}],
        "market_features": {"AAPL": {"return_1d": 0.004}},
        "fundamental_features": {"AAPL": {"net_margin": 0.24}},
        "portfolio": {"weights": {"AAPL": 0.1}, "cash_ratio": 0.9, "nav": 1_000_000.0},
        "constraints": {"long_only": True, "max_asset_weight": 0.30,
                        "max_gross_exposure": 1.00, "fee_rate": 0.001, "slippage_bps": 0.0},
        "news": [{"provider": "finnhub", "provider_news_id": "1",
                  "published_at_utc": "2026-06-05T18:15:00+00:00",
                  "available_at_utc": "2026-06-05T18:17:03+00:00",
                  "ticker": "AAPL", "tickers": ["AAPL"], "company_names": ["Apple Inc."],
                  "headline": "x"}],
    }


def test_observation_and_response_validate():
    jsonschema = pytest.importorskip("jsonschema")
    obs = _sample_observation_with_news()
    jsonschema.Draft202012Validator(_load("observation_with_news.schema.json")).validate(obs)

    resp = {"type": "decision_response", "protocol_version": "0.1", "run_id": "r",
            "team_id": "t", "session_date": "2026-06-05",
            "target_weights": {"AAPL": 0.2}}
    jsonschema.Draft202012Validator(_load("decision_response.schema.json")).validate(resp)


def test_malformed_response_rejected():
    jsonschema = pytest.importorskip("jsonschema")
    bad = {"type": "decision_response", "protocol_version": "0.1",
           "team_id": "t", "target_weights": {"AAPL": "oops"}}
    errs = list(jsonschema.Draft202012Validator(
        _load("decision_response.schema.json")).iter_errors(bad))
    assert errs  # missing run_id/session_date + non-numeric weight


def test_no_leakage_all_news_before_cutoff():
    """Every news item's available_at must be <= the decision cutoff."""
    obs = _sample_observation_with_news()
    cutoff = datetime.fromisoformat(obs["event_time_utc"])
    for item in obs["news"]:
        assert datetime.fromisoformat(item["available_at_utc"]) <= cutoff
