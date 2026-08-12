"""Lookahead / future-data leakage tests.

Exercises the REAL point-in-time guards, not a hand-made sample:
  1. security.assert_observation_point_in_time - the recursive walk that rejects any
     `*_at_utc` field dated after the decision cutoff (called on every built observation).
  2. NewsStore.load_visible - the filter that only surfaces news with
     available_at_utc <= cutoff.

Both are the actual functions the evaluator uses, so a regression that lets future data
through would fail here.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from portfolio_agent.news.models import NewsRecord
from portfolio_agent.news.store import NewsStore
from portfolio_agent.security import assert_observation_point_in_time

CUTOFF = datetime(2026, 6, 5, 19, 50, tzinfo=timezone.utc)


def _news_item(available_at: datetime) -> dict:
    return {
        "provider": "test", "provider_news_id": "1",
        "published_at_utc": available_at.isoformat(),
        "available_at_utc": available_at.isoformat(),
        "ticker": "AAPL", "tickers": ["AAPL"], "headline": "x",
    }


def test_clean_observation_passes():
    """News stamped before the cutoff must be accepted."""
    obs = {"event_time_utc": CUTOFF.isoformat(),
           "news": [_news_item(CUTOFF - timedelta(hours=1))]}
    assert_observation_point_in_time(obs, CUTOFF)  # must not raise


def test_future_news_is_rejected():
    """A news item stamped AFTER the cutoff must raise - this is the leakage guard."""
    obs = {"event_time_utc": CUTOFF.isoformat(),
           "news": [_news_item(CUTOFF + timedelta(minutes=30))]}
    with pytest.raises(ValueError, match="future data crossed agent boundary"):
        assert_observation_point_in_time(obs, CUTOFF)


def test_future_data_rejected_when_nested_deep():
    """The guard must catch a future timestamp anywhere, not just at the top level."""
    obs = {"assets": [{"ticker": "AAPL",
                       "some_report_at_utc": (CUTOFF + timedelta(days=1)).isoformat()}]}
    with pytest.raises(ValueError):
        assert_observation_point_in_time(obs, CUTOFF)


def _record(available_at: datetime) -> NewsRecord:
    return NewsRecord(
        provider="test", provider_news_id=available_at.isoformat(),
        published_at_utc=available_at, fetched_at_utc=available_at,
        first_seen_at_utc=available_at, available_at_utc=available_at,
        tickers=["AAPL"], company_names=["Apple"], headline="x", summary="",
        source="t", url="", content_hash=available_at.isoformat(), raw_path="")


def test_news_store_hides_future_news(tmp_path: Path):
    """load_visible must return only items available at or before the cutoff."""
    store = NewsStore(tmp_path)
    store.write_normalized([
        _record(CUTOFF - timedelta(hours=2)),   # visible
        _record(CUTOFF - timedelta(minutes=1)), # visible
        _record(CUTOFF + timedelta(minutes=1)), # future -> must be hidden
        _record(CUTOFF + timedelta(days=1)),    # future -> must be hidden
    ])
    visible = store.load_visible(["AAPL"], CUTOFF)
    assert len(visible) == 2
    assert all(r.available_at_utc <= CUTOFF for r in visible)
