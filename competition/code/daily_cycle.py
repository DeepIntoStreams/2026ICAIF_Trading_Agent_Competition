"""Automated competition daily cycle (CLI).

One command replays the full loop end-to-end on historical data, exactly as the deployed
server would run it day by day:

  publish   -> write the shared official observation panel + market prices for every day
  run       -> for each registered team, invoke their agent.py in isolation each day,
               validate, execute next-open, and score
  board     -> print the average-rank leaderboard

Demo (replays a prepared event log as the "market"):
  python competition/server/daily_cycle.py all \
     --event-log outputs/prototype_6mo/disciplined/event_log.json \
     --data-root data/stock_data_1y \
     --team team_example:competition/code
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine   # noqa: E402

DATA = Path(__file__).resolve().parent.parent          # competition/ root


def _parse_teams(items: list[str]) -> list[tuple[str, str]]:
    out = []
    for it in items:
        tid, _, path = it.partition(":")
        out.append((tid, path or "competition/code"))
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["publish", "run", "board", "all", "ingest-news"])
    p.add_argument("--event-log", help="event_log.json to replay as the market/news source")
    p.add_argument("--start", help="live publish: first date (YYYY-MM-DD)")
    p.add_argument("--end", help="live publish: last date (YYYY-MM-DD)")
    p.add_argument("--news-dir", help="NewsStore the PanelBuilder reads (see ingest-news)")
    p.add_argument("--data-root", default="data/stock_data_1y")
    p.add_argument("--team", action="append", default=[],
                   help="team_id:path/to/code  (repeatable)")
    p.add_argument("--source", choices=["code", "posted"], default="code",
                   help="code = run team code (validation/audit); "
                        "posted = LIVE intake, read weights the team POSTED")
    p.add_argument("--posted-dir", default=str(DATA / "posts"),
                   help="where teams' posted decision_responses live (for --source posted)")
    p.add_argument("--timeout", type=float, default=60.0)
    p.add_argument("--out", default=str(DATA))
    args = p.parse_args(argv)

    if args.command == "ingest-news":
        import ingest
        n = ingest.ingest_news(args.data_root, args.start, args.end, args.news_dir)
        print(f"[ingest-news] fetched {n} records -> {args.news_dir}")
        return 0

    if args.command in ("publish", "all"):
        if args.start and args.end:      # LIVE: build panels from fresh data
            dates = engine.publish_live(args.data_root, args.news_dir, args.start, args.end, args.out)
        elif args.event_log:             # DEMO: replay a prepared event log
            dates = engine.build_panel(args.event_log, args.data_root, args.out)
        else:
            raise SystemExit("provide --start/--end (live) or --event-log (replay)")
        print(f"[publish] wrote {len(dates)} daily panels: {dates[0]} .. {dates[-1]}")

    if args.command in ("run", "all"):
        for tid, code in _parse_teams(args.team):
            if args.source == "posted":
                s = engine.run_team(args.out, tid, args.data_root,
                                    posted_dir=args.posted_dir, timeout_seconds=args.timeout)
            else:
                s = engine.run_team(args.out, tid, args.data_root,
                                    code_dir=code, timeout_seconds=args.timeout)
            m = s["metrics"]
            print(f"[run] {tid}: M1={m['m1_cumulative_return']*100:.2f}%  "
                  f"Sharpe={m['m3_sharpe_ratio']:.2f}  MDD={m['m5_maximum_drawdown']*100:.2f}%  "
                  f"turnover={m['m8_turnover']:.2f}  M9={m['m9_violation_rate']*100:.1f}%  "
                  f"code_hash={s['code_hash'][:10]}")

    if args.command in ("board", "all"):
        board = engine.leaderboard(args.out)
        print("\n=== LEADERBOARD (avg rank across M1-M9; lower wins) ===")
        print(f"{'#':>2} {'team':<16}{'avg_rank':>9}{'M1ret%':>9}{'MDD%':>7}")
        for i, r in enumerate(board, 1):
            print(f"{i:>2} {r['team_id']:<16}{r['avg_rank']:>9.3f}"
                  f"{r['m1_cumulative_return']*100:>9.2f}{r['m5_maximum_drawdown']*100:>7.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
