from datetime import date, datetime, timezone

from portfolio_agent.news.providers.finnhub import FinnhubCompanyNewsProvider


def test_finnhub_provider_maps_company_news(monkeypatch):
    payload = [
        {
            "id": 10,
            "datetime": 1780675200,
            "headline": "Apple beats estimates",
            "summary": "Apple reports stronger services revenue.",
            "source": "UnitWire",
            "url": "https://example.com/aapl",
            "related": "AAPL",
        }
    ]

    def fake_get_json(url, params, timeout_seconds):
        assert "company-news" in url
        assert params["symbol"] == "AAPL"
        assert params["token"] == "unit"
        assert timeout_seconds == 30.0
        return payload

    monkeypatch.setattr(
        "portfolio_agent.news.providers.finnhub._get_json",
        fake_get_json,
    )
    provider = FinnhubCompanyNewsProvider(api_key="unit")
    fetched_at = datetime(2026, 6, 5, 19, 0, tzinfo=timezone.utc)
    records = provider.fetch_company_news(
        "AAPL",
        "Apple Inc.",
        date(2026, 6, 5),
        date(2026, 6, 5),
        fetched_at,
    )
    assert records[0].provider == "finnhub"
    assert records[0].provider_news_id == "10"
    assert records[0].tickers == ["AAPL"]
    assert records[0].company_names == ["Apple Inc."]
    assert records[0].available_at_utc == fetched_at
