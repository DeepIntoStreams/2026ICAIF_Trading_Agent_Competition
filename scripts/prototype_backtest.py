"""End-to-end prototype backtest: prices + news -> allocation -> next-open exec -> M1-M9.

Aligns with the official protocol (ACM ICAIF 2026 Trading Agent Competition, sec 3.2):
target weights decided by the close of day t are executed at the OPEN of day t+1, with a
0.1% fee on traded notional. The current in-repo evaluator fills at same-day close; this
runner reuses the evaluator only to build the point-in-time observations and run the agent,
then RECONSTRUCTS the next-open execution from the resulting event log + adjusted prices,
and reports the full M1-M9 panel for both execution models so the difference is visible.

Usage:
  python scripts/prototype_backtest.py --data-root data/stock_data_1y \
      --config configs/evaluation.yaml --output-root outputs/prototype_6mo \
      --set evaluation.horizon_trading_days=126 \
      --set news.data_dir=data/news/backfill_20260605_20260618
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from portfolio_agent.agents.disciplined_rule import AllocatorParams, DisciplinedAllocator
from portfolio_agent.config import load_config
from portfolio_agent.evaluator import DailyTradingEvaluator
from portfolio_agent.metrics import compute_metrics
from portfolio_agent.news.store import NewsStore

FEE_RATE = 0.001
INITIAL_CAPITAL = 1_000_000.0


def _load_adjusted_prices(data_root: str, tickers: list[str]) -> dict:
    """ticker -> {date_str: (adj_open, adj_close)} using adj_close/close as the factor."""
    root = Path(data_root) / "prices_daily"
    out: dict[str, dict[str, tuple[float, float]]] = {}
    for tk in tickers:
        path = root / f"{tk}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path)
        factor = df["adj_close"] / df["close"]
        adj_open = df["open"] * factor
        out[tk] = {
            str(pd.Timestamp(d).date()): (float(o), float(c))
            for d, o, c in zip(df["Date"], adj_open, df["adj_close"])
        }
    return out


def _reconstruct_next_open(sessions: list[dict], prices: dict) -> dict:
    """Simulate next-open execution: weights decided at close of day t fill at open of t+1."""
    dates = [s["session_date"] for s in sessions]
    weights = [s.get("sanitized_action", {}) or {} for s in sessions]

    def px(tk: str, d: str):
        return prices.get(tk, {}).get(d)

    cash = INITIAL_CAPITAL
    shares: dict[str, float] = {}
    nav_series: list[float] = []
    total_cost = 0.0
    total_traded = 0.0

    for i, d in enumerate(dates):
        if i >= 1:
            target = weights[i - 1]                      # decided at close of dates[i-1]
            nav_open = cash + sum(sh * px(t, d)[0] for t, sh in shares.items() if px(t, d))
            new_shares = dict(shares)
            for t, wt in target.items():
                o = px(t, d)
                if o and o[0] > 0:
                    new_shares[t] = (wt * nav_open) / o[0]
            traded = 0.0
            for t in set(shares) | set(new_shares):
                o = px(t, d)
                if not o:                                # untradable today: keep position
                    new_shares[t] = shares.get(t, 0.0)
                    continue
                traded += abs(new_shares.get(t, 0.0) - shares.get(t, 0.0)) * o[0]
            fee = traded * FEE_RATE
            total_cost += fee
            total_traded += traded
            invested = sum(sh * px(t, d)[0] for t, sh in new_shares.items() if px(t, d))
            cash = nav_open - invested - fee
            shares = {t: sh for t, sh in new_shares.items() if abs(sh) > 1e-12}
        nav_close = cash + sum(sh * px(t, d)[1] for t, sh in shares.items() if px(t, d))
        nav_series.append(nav_close)

    violations = sum(1 for s in sessions if s.get("agent_diagnostics", {}).get("violations"))
    return {
        "nav": nav_series,
        "total_transaction_cost": total_cost,
        "total_trade_value": total_traded,
        "violation_steps": violations,
        "decision_steps": len(sessions),
    }


def _fmt(m: dict) -> str:
    def g(k):
        v = m.get(k)
        return "n/a" if v is None else f"{v:.4f}"
    return (
        f"  M1 cumulative_return : {m['m1_cumulative_return']*100:.2f}%\n"
        f"  M2 daily_win_rate    : {m['m2_daily_win_rate']*100:.1f}%\n"
        f"  M3 sharpe            : {g('m3_sharpe_ratio')}\n"
        f"  M4 sortino           : {g('m4_sortino_ratio')}\n"
        f"  M5 max_drawdown      : {m['m5_maximum_drawdown']*100:.2f}%\n"
        f"  M6 VaR95             : {m['m6_value_at_risk_95']*100:.2f}%\n"
        f"  M7 ES95              : {m['m7_expected_shortfall_95']*100:.2f}%\n"
        f"  M8 turnover          : {m['m8_turnover']:.2f}\n"
        f"  M9 violation_rate    : {m['m9_violation_rate']*100:.1f}%\n"
        f"  sample_size          : {m.get('sample_size')}"
        + ("   [LOW SAMPLE]" if m.get("low_sample_warning") else "")
    )


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-root", required=True)
    p.add_argument("--config", default="configs/evaluation.yaml")
    p.add_argument("--output-root", required=True)
    p.add_argument("--set", action="append", default=[])
    args = p.parse_args(argv)

    config = load_config(args.config, args.set)
    out = Path(args.output_root)
    evaluator = DailyTradingEvaluator(
        data_root=args.data_root, config=config,
        news_store=NewsStore(config.news.data_dir))
    agent = DisciplinedAllocator(
        AllocatorParams(),
        max_asset_weight=config.constraints.max_asset_weight,
        max_gross_exposure=config.constraints.max_gross_exposure)

    print("Running disciplined allocator through the point-in-time evaluator ...")
    result = evaluator.run_agent(agent, "disciplined", out / "disciplined")
    same_day = result["metrics"]

    event_log = json.loads((out / "disciplined" / "event_log.json").read_text())
    sessions = event_log["sessions"]
    tickers = [a["ticker"] for a in sessions[0]["observation"]["assets"]]
    prices = _load_adjusted_prices(args.data_root, tickers)
    rec = _reconstruct_next_open(sessions, prices)
    next_open = compute_metrics(
        rec["nav"], initial_capital=INITIAL_CAPITAL,
        total_transaction_cost=rec["total_transaction_cost"],
        total_trade_value=rec["total_trade_value"],
        violation_steps=rec["violation_steps"], decision_steps=rec["decision_steps"],
        annualization=config.evaluation.annualization,
        risk_free_rate=config.evaluation.risk_free_rate)

    span = f"{sessions[0]['session_date']} -> {sessions[-1]['session_date']}"
    print(f"\n=== DISCIPLINED ALLOCATOR  ({len(sessions)} trading days, {span}) ===")
    print("\n[Same-day close execution — in-repo evaluator default]")
    print(_fmt(same_day))
    print("\n[Next-open execution — OFFICIAL protocol (paper sec 3.2)]")
    print(_fmt(next_open))

    (out / "prototype_summary.json").write_text(json.dumps(
        {"span": span, "n_days": len(sessions),
         "same_day_close": same_day, "next_open": next_open}, indent=2, default=str))
    print(f"\nSaved: {out/'prototype_summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
