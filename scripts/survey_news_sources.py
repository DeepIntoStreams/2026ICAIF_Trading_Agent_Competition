"""Empirical news-source survey (README next-step #2), execution level.

Runs the actual providers over a small ticker sample and reports measurable facts:
record count, coverage (how many sampled tickers returned anything), intraday-timestamp
availability, whether available_at is authoritative (!= just the publish date), and
wall-clock latency. Turns the #2 survey from a claims table into reproducible numbers.

Usage:
  # SEC EDGAR (free, no key, live-tested):
  python scripts/survey_news_sources.py --source sec_edgar \
      --tickers AAPL MSFT NVDA --from 2025-01-01 --to 2025-12-31 \
      --user-agent "you you@example.com"

  # Finnhub (needs FINNHUB_API_KEY):
  FINNHUB_API_KEY=... python scripts/survey_news_sources.py --source finnhub \
      --tickers AAPL MSFT --from 2026-05-01 --to 2026-06-18

  # GDELT (implemented to spec; verify reachability from your host):
  python scripts/survey_news_sources.py --source gdelt --tickers AAPL --from 2026-06-01 --to 2026-06-18
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _build(source: str, user_agent: str):
    if source == "sec_edgar":
        from portfolio_agent.news.providers.sec_edgar import SecEdgarNewsProvider
        return SecEdgarNewsProvider(user_agent=user_agent)
    if source == "gdelt":
        from portfolio_agent.news.providers.gdelt import GdeltNewsProvider
        return GdeltNewsProvider(user_agent=user_agent)
    if source == "finnhub":
        from portfolio_agent.news.providers.finnhub import FinnhubCompanyNewsProvider
        key = os.environ.get("FINNHUB_API_KEY")
        if not key:
            raise SystemExit("FINNHUB_API_KEY not set")
        return FinnhubCompanyNewsProvider(api_key=key)
    raise SystemExit(f"unknown source: {source}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", required=True, choices=["sec_edgar", "gdelt", "finnhub"])
    p.add_argument("--tickers", nargs="+", required=True)
    p.add_argument("--from", dest="from_date", required=True)
    p.add_argument("--to", dest="to_date", required=True)
    p.add_argument("--user-agent", default="portfolio-agent-survey contact@example.com")
    args = p.parse_args(argv)

    start = date.fromisoformat(args.from_date)
    end = date.fromisoformat(args.to_date)
    now = datetime.now(timezone.utc)

    provider = _build(args.source, args.user_agent)
    if args.source == "finnhub":
        fetch = lambda t: provider.fetch_company_news(t, t, start, end, now)  # noqa: E731
    else:
        fetch = lambda t: provider.fetch_company_news(t, t, start, end, now)  # noqa: E731

    total = 0
    covered = 0
    intraday = 0
    authoritative = 0
    t0 = time.time()
    per_ticker = []
    for tk in args.tickers:
        try:
            recs = fetch(tk)
        except Exception as exc:  # noqa: BLE001
            print(f"  {tk}: ERROR {type(exc).__name__}: {exc}")
            recs = []
        n = len(recs)
        total += n
        covered += 1 if n else 0
        intraday += sum(1 for r in recs if (r.available_at_utc.hour or r.available_at_utc.minute))
        authoritative += sum(1 for r in recs if r.available_at_utc == r.published_at_utc
                             and (r.available_at_utc.hour or r.available_at_utc.minute))
        per_ticker.append((tk, n))
    elapsed = time.time() - t0

    print(f"\n=== SURVEY: {args.source}  {start}..{end}  ({len(args.tickers)} tickers) ===")
    for tk, n in per_ticker:
        print(f"  {tk:<6} {n:>4} records")
    print(f"  ---")
    print(f"  total records         : {total}")
    print(f"  ticker coverage       : {covered}/{len(args.tickers)}")
    print(f"  records w/ intraday ts : {intraday} ({100*intraday//max(1,total)}%)")
    print(f"  authoritative first-seen: {authoritative} (available_at has real time-of-day)")
    print(f"  wall-clock latency     : {elapsed:.1f}s ({elapsed/max(1,len(args.tickers)):.2f}s/ticker)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
