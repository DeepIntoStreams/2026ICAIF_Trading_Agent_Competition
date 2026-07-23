"""Collect current company news into the configured NewsStore."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from portfolio_agent.config import load_config
from portfolio_agent.data_loader import flatten_universe, load_evaluation_universe
from portfolio_agent.news.normalizer import normalize_finnhub_company_news
from portfolio_agent.news.providers.finnhub import FinnhubCompanyNewsProvider
from portfolio_agent.news.store import NewsStore


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect company news.")
    parser.add_argument("--config", default="configs/evaluation.yaml")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--provider", default="finnhub", choices=["finnhub"])
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--set", action="append", default=[])
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    config = load_config(args.config, args.set)
    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        raise SystemExit("FINNHUB_API_KEY is required for Finnhub collection.")

    sectors = load_evaluation_universe(args.data_root)
    tickers = flatten_universe(sectors)
    store = NewsStore(config.news.data_dir)
    provider = FinnhubCompanyNewsProvider(api_key=api_key)
    fetched_at = datetime.now(timezone.utc)
    today = fetched_at.date()

    all_records = []
    for ticker in tickers:
        raw_payloads = provider.fetch_raw_company_news(
            ticker,
            start_date=today,
            end_date=today,
        )
        raw_path = store.write_raw(
            "finnhub",
            [
                {
                    "ticker": ticker,
                    "company_name": ticker,
                    "fetched_at_utc": fetched_at.isoformat(),
                    "payload": payload,
                }
                for payload in raw_payloads
            ],
        )
        records = normalize_finnhub_company_news(
            payloads=raw_payloads,
            ticker=ticker,
            company_name=ticker,
            fetched_at_utc=fetched_at,
            raw_path=raw_path,
            historical_backfill_mode=config.news.historical_backfill_mode,
        )
        all_records.extend(records)

    store.write_normalized(all_records)
    print(f"Collected {len(all_records)} normalized news records.")


if __name__ == "__main__":
    main()
