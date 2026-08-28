"""Live data ingestion (closes gap: 'live data ingestion').

Fetches the official inputs a real trading day needs and writes them where the PanelBuilder
reads them:

  ingest_news():   pull public news for the universe up to a date into a NewsStore
                   (SEC EDGAR is free, no key, and gives authoritative timestamps).
  ingest_market(): the prices under data_root/prices_daily ARE the market feed for the demo;
                   for a live event this is where a market-data API fetch would write the day's
                   OHLCV. Kept as a thin, explicit seam so the deploy engineer swaps one function.

The market-price CSVs already ship; only news must be fetched live, which this does.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from portfolio_agent.data_loader import flatten_universe, load_evaluation_universe
from portfolio_agent.news.providers.sec_edgar import SecEdgarNewsProvider
from portfolio_agent.news.store import NewsStore


def ingest_news(data_root: str, from_date: str, to_date: str, out_news_dir: str,
                user_agent: str = "icaif-competition contact@example.com") -> int:
    """Fetch public news (SEC EDGAR) for the universe into a NewsStore. Returns record count."""
    tickers = flatten_universe(load_evaluation_universe(data_root))
    provider = SecEdgarNewsProvider(user_agent=user_agent)
    store = NewsStore(out_news_dir)
    now = datetime.now(timezone.utc)
    start, end = date.fromisoformat(from_date), date.fromisoformat(to_date)

    all_records = []
    for tk in tickers:
        try:
            recs = provider.fetch_company_news(tk, tk, start, end, now)
        except Exception as exc:  # noqa: BLE001
            print(f"  news ingest {tk}: {type(exc).__name__}: {exc}", file=sys.stderr)
            recs = []
        all_records.extend(recs)
    if all_records:
        store.write_raw("sec_edgar", [
            {"ticker": r.tickers[0], "company_name": r.company_names[0],
             "fetched_at_utc": now.isoformat(),
             "payload": {"accession": r.provider_news_id, "headline": r.headline,
                         "available_at_utc": r.available_at_utc.isoformat()}}
            for r in all_records])
        store.write_normalized(all_records)
    return len(all_records)


def ingest_market(data_root: str, as_of_date: str) -> bool:
    """Seam for the live market feed. In the demo the CSVs under data_root ARE the feed, so
    this just confirms the day is present. Live: replace with an API fetch that appends the
    day's adjusted OHLCV to data_root/prices_daily/<ticker>.csv."""
    import pandas as pd
    tickers = flatten_universe(load_evaluation_universe(data_root))
    root = Path(data_root) / "prices_daily"
    present = 0
    for tk in tickers:
        p = root / f"{tk}.csv"
        if p.exists():
            df = pd.read_csv(p, usecols=["Date"])
            if (df["Date"] == as_of_date).any():
                present += 1
    return present > 0
