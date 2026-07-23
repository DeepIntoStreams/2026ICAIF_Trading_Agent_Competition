from datetime import datetime, timezone

from portfolio_agent.news.models import NewsRecord
from portfolio_agent.news.sentiment import score_news_by_ticker
from portfolio_agent.news.store import NewsStore


def _record(news_id: str, ticker: str, available: str, headline: str) -> NewsRecord:
    ts = datetime.fromisoformat(available.replace("Z", "+00:00"))
    return NewsRecord(
        provider="unit",
        provider_news_id=news_id,
        published_at_utc=ts,
        fetched_at_utc=ts,
        first_seen_at_utc=ts,
        available_at_utc=ts,
        tickers=[ticker],
        company_names=[ticker + " Corp"],
        headline=headline,
        summary="",
        source="unit",
        url="https://example.com/" + news_id,
        content_hash="hash-" + news_id,
        raw_path="raw.jsonl",
    )


def test_visible_news_uses_available_at_cutoff(tmp_path):
    store = NewsStore(tmp_path)
    store.write_normalized(
        [
            _record("before", "AAPL", "2026-06-05T18:00:00Z", "AAPL beats estimates"),
            _record("after", "AAPL", "2026-06-05T20:00:00Z", "AAPL guidance cut"),
        ]
    )
    cutoff = datetime(2026, 6, 5, 19, 50, tzinfo=timezone.utc)
    visible = store.load_visible(["AAPL"], cutoff)
    assert [item.provider_news_id for item in visible] == ["before"]


def test_store_dedupes_by_provider_id_and_hash(tmp_path):
    store = NewsStore(tmp_path)
    one = _record("1", "MSFT", "2026-06-05T18:00:00Z", "MSFT wins contract")
    duplicate_id = _record("1", "MSFT", "2026-06-05T18:01:00Z", "MSFT wins contract")
    duplicate_hash = _record("2", "MSFT", "2026-06-05T18:02:00Z", "MSFT wins contract")
    duplicate_hash.content_hash = one.content_hash
    store.write_normalized([one, duplicate_id, duplicate_hash])
    visible = store.load_visible(
        ["MSFT"],
        datetime(2026, 6, 5, 19, 0, tzinfo=timezone.utc),
    )
    assert len(visible) == 1


def test_store_merges_ticker_mappings_for_duplicate_story_hash(tmp_path):
    store = NewsStore(tmp_path)
    one = _record("1", "AAPL", "2026-06-05T18:00:00Z", "Mega cap wins contract")
    two = _record("2", "MSFT", "2026-06-05T18:01:00Z", "Mega cap wins contract")
    two.content_hash = one.content_hash
    store.write_normalized([one, two])
    visible = store.load_visible(
        ["AAPL", "MSFT"],
        datetime(2026, 6, 5, 19, 0, tzinfo=timezone.utc),
    )
    assert len(visible) == 1
    assert visible[0].tickers == ["AAPL", "MSFT"]


def test_simple_sentiment_scores_positive_and_negative_news():
    records = [
        _record("good", "NVDA", "2026-06-05T18:00:00Z", "NVDA raises guidance after earnings beat"),
        _record("bad", "TSLA", "2026-06-05T18:00:00Z", "TSLA faces probe after recall"),
    ]
    scores = score_news_by_ticker(records)
    assert scores["NVDA"].sentiment > 0
    assert scores["TSLA"].sentiment < 0
