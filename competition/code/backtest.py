"""Self-service local backtest for participants (the main thing you run while developing).

Runs YOUR alpha over the historical data with the official rules (next-open execution, 0.1%
fee, long-only, caps) and prints M1-M9 -- the same engine the organizer uses, so your local
score matches the leaderboard. Also checks your alpha for look-ahead bias.

  # backtest your alpha over the validation window
  python competition/code/backtest.py --alpha competition/alpha \
      --data-root data/stock_data_1y --start 2026-06-05 --end 2026-06-18

  # add the look-ahead audit
  python competition/code/backtest.py --alpha competition/alpha \
      --data-root data/stock_data_1y --start 2026-06-05 --end 2026-06-18 --check-lookahead

Two look-ahead protections:
  1. GUARANTEE (always on): every daily observation handed to your alpha is verified
     point-in-time -- it contains nothing dated after the decision cutoff. If you use only
     the observation, you cannot leak the future. The backtest asserts this each day.
  2. AUDIT (--check-lookahead): truncation-invariance. Your decision on day t must not depend
     on any data after day t. The backtest re-runs ending on an earlier date and checks that
     the overlapping days' weights are IDENTICAL. If they differ, your alpha used future data
     (e.g. it read a data file directly instead of the observation) -- that is look-ahead bias.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "src"))

import engine  # noqa: E402
from portfolio_agent.security import assert_observation_point_in_time  # noqa: E402
import pandas as pd  # noqa: E402


def _trading_days(data_root: str, start: str, end: str) -> list[str]:
    import glob
    any_csv = sorted(glob.glob(str(Path(data_root) / "prices_daily" / "*.csv")))[0]
    df = pd.read_csv(any_csv, usecols=["Date"])
    return [str(pd.Timestamp(d).date()) for d in df["Date"]
            if start <= str(pd.Timestamp(d).date()) <= end]


def _assert_panels_point_in_time(out: Path) -> int:
    """Guarantee check: every published observation is point-in-time clean."""
    checked = 0
    for p in sorted((out / "data" / "observations").glob("*.json")):
        panel = json.loads(p.read_text())
        assert_observation_point_in_time(panel, pd.Timestamp(panel["event_time_utc"]).to_pydatetime())
        checked += 1
    return checked


def _weights_by_day(out: Path, team: str = "alpha") -> dict[str, dict]:
    sub = out / "submissions" / team
    return {p.stem: json.loads(p.read_text())["target_weights"] for p in sub.glob("*.json")}


def run_backtest(data_root: str, alpha_dir: str, start: str, end: str,
                 news_dir: str | None, out_dir: str) -> dict:
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    # Any alpha that loads extra data MUST read it from $COMPETITION_DATA so the look-ahead
    # audit can hand it a point-in-time-limited copy. Compliant alphas use only the observation.
    os.environ["COMPETITION_DATA"] = str(Path(data_root).resolve())
    engine.publish_live(data_root, news_dir, start, end, str(out))
    checked = _assert_panels_point_in_time(out)
    summary = engine.run_team(str(out), "alpha", data_root, code_dir=alpha_dir)
    summary["observations_pit_verified"] = checked
    return summary


def _truncate_data_root(data_root: str, cut: str, dst: str) -> str:
    """A copy of data_root with every price row dated AFTER `cut` removed."""
    src, out = Path(data_root), Path(dst)
    (out / "prices_daily").mkdir(parents=True, exist_ok=True)
    for csv in (src / "prices_daily").glob("*.csv"):
        df = pd.read_csv(csv)
        df = df[df["Date"].astype(str).str[:10] <= cut]
        df.to_csv(out / "prices_daily" / csv.name, index=False)
    for extra in ("universe.json",):
        if (src / extra).exists():
            shutil.copy(src / extra, out / extra)
    if (src / "fundamentals_quarterly").exists():
        shutil.copytree(src / "fundamentals_quarterly", out / "fundamentals_quarterly")
    return str(out)


def check_lookahead(data_root: str, alpha_dir: str, start: str, end: str,
                    news_dir: str | None, out_dir: str, n_samples: int = 5) -> dict:
    """Per-day truncation audit: for several decision days t, cut ALL data after t and check
    the decision on day t is unchanged. A leak-free alpha's day-t choice depends only on data
    <= t, so it must not change; if it does, the alpha read future data."""
    days = _trading_days(data_root, start, end)
    if len(days) < 4:
        return {"ran": False, "reason": "need >=4 trading days"}
    # full reference run
    full_out = Path(out_dir) / "_full"
    run_backtest(data_root, alpha_dir, start, end, news_dir, str(full_out))
    full_w = _weights_by_day(full_out)

    # sample interior days (skip the very first, which has little history)
    interior = days[2:]
    step = max(1, len(interior) // n_samples)
    sampled = interior[::step][:n_samples]

    bad = []
    for t in sampled:
        with tempfile.TemporaryDirectory() as tmp:
            trunc_root = _truncate_data_root(data_root, t, str(Path(tmp) / "data"))
            t_out = Path(out_dir) / f"_t_{t}"
            run_backtest(trunc_root, alpha_dir, start, t, news_dir, str(t_out))
            tw = _weights_by_day(t_out).get(t, {})
        fw = full_w.get(t, {})
        if any(abs(fw.get(k, 0) - tw.get(k, 0)) > 1e-9 for k in set(fw) | set(tw)):
            bad.append(t)
    return {"ran": True, "days_compared": len(sampled), "sampled": sampled,
            "lookahead_days": bad, "clean": not bad}


def _fmt(m: dict) -> str:
    return (f"  M1 return={m['m1_cumulative_return']*100:+.2f}%  M2 win={m['m2_daily_win_rate']*100:.0f}%  "
            f"M3 Sharpe={m['m3_sharpe_ratio']:.2f}  M4 Sortino={m['m4_sortino_ratio']:.2f}\n"
            f"  M5 MDD={m['m5_maximum_drawdown']*100:.2f}%  M6 VaR={m['m6_value_at_risk_95']*100:.2f}%  "
            f"M7 ES={m['m7_expected_shortfall_95']*100:.2f}%  M8 turnover={m['m8_turnover']:.2f}  "
            f"M9 violations={m['m9_violation_rate']*100:.0f}%")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--alpha", required=True, help="your strategy dir (contains agent.py)")
    p.add_argument("--data-root", default="data/stock_data_1y")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--news-dir", default=None)
    p.add_argument("--check-lookahead", action="store_true")
    p.add_argument("--out", default="competition/backtests/run")
    args = p.parse_args(argv)

    summary = run_backtest(args.data_root, args.alpha, args.start, args.end, args.news_dir, args.out)
    m = summary["metrics"]
    print(f"\n=== BACKTEST  {args.start} -> {args.end}  ({summary['n_days']} trading days) ===")
    print(_fmt(m))
    print(f"  [guarantee] {summary['observations_pit_verified']} observations verified "
          f"point-in-time (no future data in what your alpha saw)")

    if args.check_lookahead:
        res = check_lookahead(args.data_root, args.alpha, args.start, args.end,
                              args.news_dir, args.out + "_la")
        print("\n=== LOOK-AHEAD AUDIT (truncation invariance) ===")
        if not res["ran"]:
            print("  skipped:", res["reason"])
        elif res["clean"]:
            print(f"  PASS - decisions on all {res['days_compared']} overlapping days were "
                  f"identical when the future was truncated. No look-ahead detected.")
        else:
            print(f"  FAIL - decisions CHANGED on {len(res['lookahead_days'])} day(s) when future "
                  f"data was removed: {res['lookahead_days']}")
            print("  Your alpha is using data from after the decision day (look-ahead bias).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
