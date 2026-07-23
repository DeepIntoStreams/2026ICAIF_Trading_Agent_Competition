from datetime import datetime, timezone

import pytest

from portfolio_agent.security import assert_observation_point_in_time, make_asset_id


def test_asset_id_is_stable_and_does_not_expose_ticker():
    asset_id = make_asset_id("TEST_ASSET", b"unit-test-secret")
    assert asset_id == make_asset_id("TEST_ASSET", b"unit-test-secret")
    assert "TEST_ASSET" not in asset_id


def test_ticker_company_and_raw_news_are_allowed_when_available():
    cutoff = datetime(2026, 6, 5, 19, 50, tzinfo=timezone.utc)
    observation = {
        "event_time_utc": cutoff.isoformat(),
        "assets": [
            {
                "ticker": "AAPL",
                "company_name": "Apple Inc.",
                "open_price": 100.0,
            }
        ],
        "news": [
            {
                "ticker": "AAPL",
                "headline": "Apple beats estimates",
                "available_at_utc": cutoff.isoformat(),
            }
        ],
    }
    assert_observation_point_in_time(observation, cutoff)


def test_future_news_is_rejected():
    cutoff = datetime(2026, 6, 5, 19, 50, tzinfo=timezone.utc)
    observation = {
        "event_time_utc": cutoff.isoformat(),
        "news": [
            {
                "ticker": "AAPL",
                "headline": "Future",
                "available_at_utc": "2026-06-05T20:01:00+00:00",
            }
        ],
    }
    with pytest.raises(ValueError, match="future"):
        assert_observation_point_in_time(observation, cutoff)


def test_different_secrets_produce_different_ids():
    id1 = make_asset_id("AAPL", b"secret-1")
    id2 = make_asset_id("AAPL", b"secret-2")
    assert id1 != id2
