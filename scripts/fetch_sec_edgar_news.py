"""Fetch SEC EDGAR filings as news for a list of tickers (README #2), execution level.

Query a set of tickers over a date range and get their filings back - printed as a table,
as JSON, or written into a NewsStore so the evaluator can consume them like any other news
source. Free, no API key (SEC only asks for a descriptive User-Agent with a contact email).

Examples:
  # Print a table for a few tickers
  python scripts/fetch_sec_edgar_news.py --tickers AAPL MSFT NVDA \
      --from 2025-06-01 --to 2025-12-31 --user-agent "you you@example.com"

  # Use the whole evaluation universe instead of a hand list
  python scripts/fetch_sec_edgar_news.py --data-root data/stock_data_1y \
      --from 2025-06-01 --to 2025-12-31 --user-agent "you you@example.com"

  # Save into a NewsStore the evaluator can point at (news.data_dir)
  python scripts/fetch_sec_edgar_news.py --tickers AAPL MSFT \
      --from 2025-06-01 --to 2025-12-31 --user-agent "you you@example.com" \
      --out data/news/sec_edgar_2025H2

  # Machine-readable
  python scripts/fetch_sec_edgar_news.py --tickers AAPL --from 2025-06-01 --to 2025-12-31 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from portfolio_agent.news.providers.sec_edgar import DEFAULT_FORMS, SecEdgarNewsProvider


def _resolve_tickers(args) -> list[str]:
    if args.tickers:
        return [t.upper() for t in args.tickers]
    if args.data_root:
        from portfolio_agent.data_loader import flatten_universe, load_evaluation_universe
        return flatten_universe(load_evaluation_universe(args.data_root))
    raise SystemExit("Provide --tickers or --data-root.")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_argument_group("tickers (choose one)")
    src.add_argument("--tickers", nargs="+", help="Explicit ticker list.")
    src.add_argument("--data-root", help="Use the evaluation universe from this data root.")
    p.add_argument("--from", dest="from_date", required=True, help="YYYY-MM-DD (inclusive).")
    p.add_argument("--to", dest="to_date", required=True, help="YYYY-MM-DD (inclusive).")
    p.add_argument("--forms", nargs="+", default=list(DEFAULT_FORMS),
                   help=f"Filing forms to include (default: {' '.join(DEFAULT_FORMS)}).")
    p.add_argument("--user-agent", default="portfolio-agent-fetch contact@example.com",
                   help="SEC requires a descriptive UA with a real contact email.")
    p.add_argument("--limit", type=int, default=8, help="Max rows printed per ticker.")
    p.add_argument("--json", action="store_true", help="Emit JSONL of all records to stdout.")
    p.add_argument("--out", help="Write a NewsStore at this directory (raw + normalized).")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    tickers = _resolve_tickers(args)
    start = date.fromisoformat(args.from_date)
    end = date.fromisoformat(args.to_date)
    now = datetime.now(timezone.utc)

    provider = SecEdgarNewsProvider(user_agent=args.user_agent, forms=tuple(args.forms))

    all_records = []
    per_ticker: list[tuple[str, list]] = []
    for tk in tickers:
        try:
            recs = provider.fetch_company_news(tk, tk, start, end, now)
        except Exception as exc:  # noqa: BLE001
            print(f"  {tk}: ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
            recs = []
        recs.sort(key=lambda r: r.available_at_utc, reverse=True)
        per_ticker.append((tk, recs))
        all_records.extend(recs)

    # --- output modes ---
    if args.json:
        for r in all_records:
            print(json.dumps(r.to_dict()))
        return 0

    if args.out:
        from portfolio_agent.news.store import NewsStore
        store = NewsStore(args.out)
        store.write_raw("sec_edgar", [
            {"ticker": r.tickers[0], "company_name": r.company_names[0],
             "fetched_at_utc": now.isoformat(),
             "payload": {"accession": r.provider_news_id, "headline": r.headline,
                         "available_at_utc": r.available_at_utc.isoformat(), "url": r.url}}
            for r in all_records])
        store.write_normalized(all_records)
        print(f"Wrote {len(all_records)} records to NewsStore at {args.out}")
        print(f"Point the evaluator at it:  --set news.data_dir={args.out}")
        return 0

    # default: human table
    print(f"\nSEC EDGAR filings  {start} .. {end}   forms={','.join(args.forms)}")
    for tk, recs in per_ticker:
        print(f"\n{tk}  ({len(recs)} filings)")
        for r in recs[: args.limit]:
            print(f"  {r.available_at_utc.strftime('%Y-%m-%d %H:%M')}Z  {r.headline[:64]}")
        if len(recs) > args.limit:
            print(f"  ... +{len(recs) - args.limit} more")
    print(f"\nTotal: {len(all_records)} filings across {len(tickers)} tickers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
