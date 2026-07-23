"""Validate market and news data for an evaluation run."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from portfolio_agent.config import load_config
from portfolio_agent.data_loader import align_trading_dates, flatten_universe, load_evaluation_universe, load_price_data
from portfolio_agent.market_calendar import select_evaluation_sessions, session_clock
from portfolio_agent.news.store import NewsStore


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate evaluation dataset.")
    parser.add_argument("--config", default="configs/evaluation.yaml")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--set", action="append", default=[])
    return parser.parse_args(argv)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    config = load_config(args.config, args.set)
    sectors = load_evaluation_universe(args.data_root)
    tickers = flatten_universe(sectors)
    raw_prices = load_price_data(args.data_root, tickers)
    calendar, _ = align_trading_dates(raw_prices)
    sessions = select_evaluation_sessions(calendar, config.evaluation)

    store = NewsStore(config.news.data_dir)
    news_records = store.load_all()
    missing_available_at = [
        record.provider_news_id
        for record in news_records
        if record.available_at_utc is None
    ]

    leakage = []
    output_root = Path(args.output_root)
    cutoff_by_session = {
        session.date().isoformat(): session_clock(
            session,
            config.evaluation.decision_minutes_before_close,
        ).decision_cutoff_utc
        for session in sessions
    }
    for news_seen_path in output_root.glob("*/news_seen.jsonl"):
        with open(news_seen_path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                item = json.loads(line)
                session_date = item.get("session_date")
                cutoff = cutoff_by_session.get(session_date)
                if cutoff is None:
                    continue
                available_at = _parse_time(item["available_at_utc"])
                if available_at > cutoff:
                    leakage.append(
                        {
                            "path": str(news_seen_path),
                            "session_date": session_date,
                            "provider_news_id": item.get("provider_news_id"),
                            "available_at_utc": item.get("available_at_utc"),
                            "cutoff_utc": cutoff.isoformat(),
                        }
                    )

    report = {
        "selected_sessions": [session.date().isoformat() for session in sessions],
        "news_record_count": len(news_records),
        "missing_available_at": missing_available_at,
        "future_news_leakage": leakage,
        "valid": not missing_available_at and not leakage,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    with open(output_root / "news_coverage_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    if not report["valid"]:
        raise SystemExit("Dataset validation failed. See news_coverage_report.json.")
    print(f"Dataset validation passed for {len(sessions)} sessions.")


if __name__ == "__main__":
    main()
