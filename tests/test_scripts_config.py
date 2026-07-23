from scripts.run_evaluation import parse_args


def test_run_evaluation_accepts_repeated_overrides():
    args = parse_args(
        [
            "--data-root",
            "data/stock_data_1y",
            "--output-root",
            "outputs/test",
            "--set",
            "evaluation.horizon_trading_days=5",
            "--set",
            "agents.llm.model=local/model",
        ]
    )
    assert args.set == [
        "evaluation.horizon_trading_days=5",
        "agents.llm.model=local/model",
    ]


def test_collect_news_persists_raw_payloads(tmp_path, monkeypatch):
    from scripts import collect_news
    from portfolio_agent.news.store import NewsStore

    payload = [
        {
            "id": 1,
            "datetime": 1780675200,
            "headline": "Apple beats estimates",
            "summary": "",
            "source": "UnitWire",
            "url": "https://example.com/aapl",
        }
    ]

    monkeypatch.setenv("FINNHUB_API_KEY", "unit")
    monkeypatch.setattr(
        collect_news,
        "load_evaluation_universe",
        lambda data_root: {"Technology": ["AAPL"]},
    )
    monkeypatch.setattr(
        "portfolio_agent.news.providers.finnhub.FinnhubCompanyNewsProvider.fetch_raw_company_news",
        lambda self, ticker, start_date, end_date: payload,
    )

    collect_news.main(
        [
            "--data-root",
            "unused",
            "--set",
            f"news.data_dir={tmp_path.as_posix()}",
            "--once",
        ]
    )
    store = NewsStore(tmp_path)
    records = store.load_all()
    assert (tmp_path / "raw" / "finnhub.jsonl").exists()
    assert records[0].raw_path
