from datetime import datetime, timezone

from portfolio_agent.news.models import NewsRecord
from portfolio_agent.observation import build_decision_observation


def test_decision_observation_filters_news_by_cutoff():
    cutoff = datetime(2026, 6, 5, 19, 50, tzinfo=timezone.utc)
    before = NewsRecord(
        "unit",
        "1",
        cutoff,
        cutoff,
        cutoff,
        cutoff,
        ["AAPL"],
        ["Apple Inc."],
        "Apple beat",
        "",
        "unit",
        "",
        "h1",
        "",
    )
    after_time = datetime(2026, 6, 5, 20, 1, tzinfo=timezone.utc)
    after = NewsRecord(
        "unit",
        "2",
        after_time,
        after_time,
        after_time,
        after_time,
        ["AAPL"],
        ["Apple Inc."],
        "Future news",
        "",
        "unit",
        "",
        "h2",
        "",
    )
    obs = build_decision_observation(
        session_date="2026-06-05",
        event_time_utc=cutoff,
        universe=[{"ticker": "AAPL", "company_name": "Apple Inc."}],
        open_prices={"AAPL": 100.0},
        market_features={"AAPL": {"return_1d": 0.01}},
        fundamental_features={"AAPL": {"net_margin": 0.2}},
        portfolio={
            "weights": {"AAPL": 0.0},
            "cash_ratio": 1.0,
            "nav": 1_000_000.0,
        },
        constraints={"max_asset_weight": 0.30},
        news=[before, after],
        max_news_items=40,
    )
    assert [n["provider_news_id"] for n in obs["news"]] == ["1"]
    assert obs["assets"][0]["ticker"] == "AAPL"


def test_decision_observation_keeps_latest_news_when_capped():
    cutoff = datetime(2026, 6, 5, 19, 50, tzinfo=timezone.utc)
    older_time = datetime(2026, 6, 5, 18, 0, tzinfo=timezone.utc)
    newer_time = datetime(2026, 6, 5, 19, 0, tzinfo=timezone.utc)
    older = NewsRecord("unit", "old", older_time, older_time, older_time, older_time, ["AAPL"], ["Apple Inc."], "Old news", "", "unit", "", "h1", "")
    newer = NewsRecord("unit", "new", newer_time, newer_time, newer_time, newer_time, ["AAPL"], ["Apple Inc."], "New news", "", "unit", "", "h2", "")
    obs = build_decision_observation(
        session_date="2026-06-05",
        event_time_utc=cutoff,
        universe=[{"ticker": "AAPL", "company_name": "Apple Inc."}],
        open_prices={"AAPL": 100.0},
        market_features={"AAPL": {}},
        fundamental_features={"AAPL": {}},
        portfolio={"weights": {"AAPL": 0.0}, "cash_ratio": 1.0, "nav": 1_000_000.0},
        constraints={"max_asset_weight": 0.30},
        news=[older, newer],
        max_news_items=1,
    )
    assert [n["provider_news_id"] for n in obs["news"]] == ["new"]


def test_decision_observation_can_hide_raw_news_text():
    cutoff = datetime(2026, 6, 5, 19, 50, tzinfo=timezone.utc)
    record = NewsRecord("unit", "1", cutoff, cutoff, cutoff, cutoff, ["AAPL"], ["Apple Inc."], "Raw headline", "Raw summary", "unit", "", "h1", "")
    obs = build_decision_observation(
        session_date="2026-06-05",
        event_time_utc=cutoff,
        universe=[{"ticker": "AAPL", "company_name": "Apple Inc."}],
        open_prices={"AAPL": 100.0},
        market_features={"AAPL": {}},
        fundamental_features={"AAPL": {}},
        portfolio={"weights": {"AAPL": 0.0}, "cash_ratio": 1.0, "nav": 1_000_000.0},
        constraints={"max_asset_weight": 0.30},
        news=[record],
        max_news_items=40,
        include_raw_text=False,
    )
    assert obs["news"][0]["headline"] == ""
    assert obs["news"][0]["summary"] == ""


def test_decision_observation_allows_backfilled_available_news_without_first_seen():
    cutoff = datetime(2026, 6, 5, 19, 50, tzinfo=timezone.utc)
    published_at = datetime(2026, 6, 5, 18, 0, tzinfo=timezone.utc)
    fetched_later = datetime(2026, 7, 23, 18, 0, tzinfo=timezone.utc)
    record = NewsRecord(
        "unit",
        "backfilled",
        published_at,
        fetched_later,
        fetched_later,
        published_at,
        ["AAPL"],
        ["Apple Inc."],
        "Backfilled but available news",
        "",
        "unit",
        "",
        "h1",
        "",
    )
    obs = build_decision_observation(
        session_date="2026-06-05",
        event_time_utc=cutoff,
        universe=[{"ticker": "AAPL", "company_name": "Apple Inc."}],
        open_prices={"AAPL": 100.0},
        market_features={"AAPL": {}},
        fundamental_features={"AAPL": {}},
        portfolio={"weights": {"AAPL": 0.0}, "cash_ratio": 1.0, "nav": 1_000_000.0},
        constraints={"max_asset_weight": 0.30},
        news=[record],
        max_news_items=40,
    )
    assert obs["news"][0]["provider_news_id"] == "backfilled"
    assert "first_seen_at_utc" not in obs["news"][0]
