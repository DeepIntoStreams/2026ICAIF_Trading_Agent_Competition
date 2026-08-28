"""Competition engine: the automated daily loop (organizer side).

Mirrors the live timeline (paper Fig. 3) as a deterministic replay so we can run the whole
flow with no web server yet:

  build_panel(): publish the SHARED official data each day  -> data/observations/<date>.json
                 (market features + PIT news, identical for every team) and the official
                 market prices -> data/market/daily/<date>.json.
  run_team():    per team, walk the days in order holding that team's own portfolio state;
                 each day build the team's decision_request (shared panel + their portfolio),
                 invoke their agent.py in isolation (sdk.runner), validate, queue the weights,
                 and execute the PREVIOUS day's weights at today's OPEN (next-open, 0.1% fee).
                 If the agent times out / crashes / returns junk -> retain previous weights.
  score():       M1-M9 per team + the average-rank leaderboard (paper sec 3.3).

Swapping to a real web server later replaces only "invoke agent.py in isolation" (an HTTP
call to the team's endpoint) and "publish files" (serve them) -- the loop is unchanged.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent                 # competition/code
sys.path.insert(0, str(HERE))                          # sibling framework modules
sys.path.insert(0, str(HERE.parents[1] / "src"))       # portfolio_agent (repo/src)

from runner import code_hash, run_agent                # competition/code/runner.py
from portfolio_agent.metrics import compute_metrics
from portfolio_agent.risk import sanitize_target_weights

FEE_RATE = 0.001
INITIAL_CAPITAL = 1_000_000.0
PANEL_KEYS = ("session_date", "event_time_utc", "assets", "market_features",
              "fundamental_features", "constraints", "news")


def _adjusted_prices(data_root: str, tickers: list[str]) -> dict[str, dict[str, tuple[float, float]]]:
    root = Path(data_root) / "prices_daily"
    out: dict[str, dict[str, tuple[float, float]]] = {}
    for tk in tickers:
        path = root / f"{tk}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path)
        factor = df["adj_close"] / df["close"]
        adj_open = df["open"] * factor
        out[tk] = {str(pd.Timestamp(d).date()): (float(o), float(c))
                   for d, o, c in zip(df["Date"], adj_open, df["adj_close"])}
    return out


def build_panel(event_log_path: str, data_root: str, out_dir: str) -> list[str]:
    """Publish the shared daily observation panel + official market prices."""
    out = Path(out_dir)
    obs_dir = out / "data" / "observations"
    mkt_dir = out / "data" / "market" / "daily"
    obs_dir.mkdir(parents=True, exist_ok=True)
    mkt_dir.mkdir(parents=True, exist_ok=True)

    ev = json.loads(Path(event_log_path).read_text())
    sessions = ev["sessions"]
    tickers = [a["ticker"] for a in sessions[0]["observation"]["assets"]]
    prices = _adjusted_prices(data_root, tickers)

    dates = []
    for s in sessions:
        d = s["session_date"]
        dates.append(d)
        panel = {k: s["observation"][k] for k in PANEL_KEYS}
        (obs_dir / f"{d}.json").write_text(json.dumps(panel))
        md = {t: {"adj_open": prices[t][d][0], "adj_close": prices[t][d][1]}
              for t in tickers if d in prices.get(t, {})}
        (mkt_dir / f"{d}.json").write_text(json.dumps(md))
    return dates


def publish_live(data_root: str, news_dir: str | None, start: str, end: str,
                 out_dir: str) -> list[str]:
    """Publish panels for every trading day in [start, end] from FRESH data (no replay)."""
    from panel import PanelBuilder
    pb = PanelBuilder(data_root, news_dir)
    df = pd.read_csv(Path(data_root) / "prices_daily" / f"{pb.tickers[0]}.csv",
                     usecols=["Date"])
    dates = [str(pd.Timestamp(d).date()) for d in df["Date"]
             if start <= str(pd.Timestamp(d).date()) <= end]
    for d in dates:
        pb.publish(out_dir, d)
    return dates


def _weights_from_shares(shares: dict[str, float], px: dict[str, float], nav: float) -> dict[str, float]:
    return {t: (sh * px[t] / nav) for t, sh in shares.items() if t in px and nav > 0}


def run_team(out_dir: str, team_id: str, data_root: str,
             code_dir: str | None = None, posted_dir: str | None = None,
             timeout_seconds: float = 60.0) -> dict[str, Any]:
    """Drive one team through the days, executing next-open and scoring.

    Weight source (exactly one):
      posted_dir : LIVE intake. Read the decision_response the team POSTED for each day
                   (organizer never runs team code). Missing post -> retain previous weights.
      code_dir   : SIMULATION / AUDIT. Run the team's agent.py to obtain weights (used by the
                   validation environment and the reproducibility audit, NOT the live event).

    Each day the organizer's OUTGOING request (shared panel + this team's portfolio state) is
    recorded to requests/<team>/<date>.json, so there is a record of exactly what the team saw.
    """
    if bool(code_dir) == bool(posted_dir):
        raise ValueError("provide exactly one of code_dir (simulate/audit) or posted_dir (live)")
    out = Path(out_dir)
    obs_dir = out / "data" / "observations"
    mkt_dir = out / "data" / "market" / "daily"
    req_dir = out / "requests" / team_id       # organizer -> team (what we sent)
    sub_dir = out / "submissions" / team_id     # server-side validated ledger
    req_dir.mkdir(parents=True, exist_ok=True)
    sub_dir.mkdir(parents=True, exist_ok=True)

    dates = sorted(p.stem for p in obs_dir.glob("*.json"))
    panels = {d: json.loads((obs_dir / f"{d}.json").read_text()) for d in dates}
    market = {d: json.loads((mkt_dir / f"{d}.json").read_text()) for d in dates}
    tickers = [a["ticker"] for a in panels[dates[0]]["assets"]]
    constraints = panels[dates[0]]["constraints"]
    cap = float(constraints.get("max_asset_weight", 0.10))
    gross_cap = float(constraints.get("max_gross_exposure", 1.00))

    def op(d, t): return market[d].get(t, {}).get("adj_open")
    def cl(d, t): return market[d].get(t, {}).get("adj_close")

    cash = INITIAL_CAPITAL
    shares: dict[str, float] = {}
    queued: dict[str, float] | None = None       # weights decided yesterday, fill at today's open
    prev_target: dict[str, float] = {}
    nav_series: list[float] = []
    total_cost = total_traded = 0.0
    violation_days = 0

    for d in dates:
        # 1) settle yesterday's decision at today's OPEN
        if queued is not None:
            nav_open = cash + sum(sh * op(d, t) for t, sh in shares.items() if op(d, t))
            new_shares = dict(shares)
            for t, w in queued.items():
                o = op(d, t)
                if o and o > 0:
                    new_shares[t] = w * nav_open / o
            traded = 0.0
            for t in set(shares) | set(new_shares):
                o = op(d, t)
                if not o:
                    new_shares[t] = shares.get(t, 0.0)
                    continue
                traded += abs(new_shares.get(t, 0.0) - shares.get(t, 0.0)) * o
            fee = traded * FEE_RATE
            total_cost += fee
            total_traded += traded
            invested = sum(sh * op(d, t) for t, sh in new_shares.items() if op(d, t))
            cash = nav_open - invested - fee
            shares = {t: sh for t, sh in new_shares.items() if abs(sh) > 1e-12}

        # 2) build + RECORD the organizer's outgoing request (shared panel + portfolio state)
        open_px = {t: op(d, t) for t in tickers if op(d, t)}
        nav_now = cash + sum(sh * open_px[t] for t, sh in shares.items() if t in open_px)
        observation = dict(panels[d])
        observation["portfolio"] = {
            "weights": _weights_from_shares(shares, open_px, nav_now),
            "cash_ratio": (cash / nav_now) if nav_now > 0 else 1.0,
            "nav": nav_now,
        }
        request = {"type": "decision_request", "protocol_version": "0.1",
                   "team_id": team_id, "session_date": d,
                   "deadline_utc": observation.get("event_time_utc"),
                   "observation": observation}
        (req_dir / f"{d}.json").write_text(json.dumps(request))

        # 3) obtain the team's weights
        if posted_dir is not None:                        # LIVE INTAKE: read what the team POSTED
            post_path = Path(posted_dir) / team_id / f"{d}.json"
            if post_path.exists():
                post = json.loads(post_path.read_text())
                raw = post.get("target_weights", {}) or {}
                audit = {"source": "posted", "post_path": str(post_path),
                         "agent_version": post.get("metadata", {}).get("agent_version")}
                ok = True
            else:
                raw, ok = dict(prev_target), False        # no submission -> retain previous
                audit = {"source": "posted", "reason": "no_submission"}
        else:                                             # SIMULATE / AUDIT: run their code
            res = run_agent(code_dir, observation, panels[d]["assets"], constraints, timeout_seconds)
            ok = res["ok"]
            audit = res["audit"]
            raw = res["target_weights"] if ok else dict(prev_target)
            if not ok:
                audit["carried_previous"] = True

        # 4) validate / repair -> M9
        sanitized, violations = sanitize_target_weights(raw, tickers, cap, gross_cap)
        if (not ok) or violations:
            violation_days += 1
        prev_target = dict(sanitized)
        queued = dict(sanitized)

        # 5) record the server-side validated ledger entry (the reproducible trail)
        (sub_dir / f"{d}.json").write_text(json.dumps({
            "type": "decision_response", "protocol_version": "0.1",
            "team_id": team_id, "session_date": d,
            "target_weights": sanitized,
            "server": {"violations": violations, "ok": ok, "audit": audit},
        }, indent=2))

        # 6) mark NAV at today's CLOSE
        nav_close = cash + sum(sh * cl(d, t) for t, sh in shares.items() if cl(d, t))
        nav_series.append(nav_close)

    metrics = compute_metrics(
        nav_series, initial_capital=INITIAL_CAPITAL,
        total_transaction_cost=total_cost, total_trade_value=total_traded,
        violation_steps=violation_days, decision_steps=len(dates))
    summary = {"team_id": team_id,
               "code_hash": code_hash(code_dir) if code_dir else "posted",
               "source": "posted" if posted_dir else "code",
               "n_days": len(dates), "metrics": metrics,
               "final_nav": nav_series[-1],
               "dates": dates, "nav_series": [round(v, 2) for v in nav_series]}
    (out / "results").mkdir(parents=True, exist_ok=True)
    (out / "results" / f"{team_id}.json").write_text(json.dumps(summary, indent=2, default=str))
    return summary


# metric direction for the rank score: True = higher is better
_HIGHER_BETTER = {
    "m1_cumulative_return": True, "m2_daily_win_rate": True,
    "m3_sharpe_ratio": True, "m4_sortino_ratio": True,
    "m5_maximum_drawdown": False, "m6_value_at_risk_95": False,
    "m7_expected_shortfall_95": False, "m8_turnover": False, "m9_violation_rate": False,
}


def leaderboard(out_dir: str) -> list[dict[str, Any]]:
    """Average-rank leaderboard across M1-M9 (paper sec 3.3; lower avg rank wins)."""
    results_dir = Path(out_dir) / "results"
    teams = [json.loads(p.read_text()) for p in results_dir.glob("*.json")]
    if not teams:
        return []
    ranks = {t["team_id"]: {} for t in teams}
    for key, higher in _HIGHER_BETTER.items():
        vals = {t["team_id"]: (t["metrics"].get(key) if t["metrics"].get(key) is not None
                               else (-1e9 if higher else 1e9)) for t in teams}
        order = sorted(vals, key=lambda tid: (-vals[tid] if higher else vals[tid]))
        for i, tid in enumerate(order, 1):
            ranks[tid][key] = i
    board = []
    for t in teams:
        tid = t["team_id"]
        avg = sum(ranks[tid].values()) / len(_HIGHER_BETTER)
        board.append({"team_id": tid, "avg_rank": round(avg, 3),
                      "m1_cumulative_return": t["metrics"]["m1_cumulative_return"],
                      "m5_maximum_drawdown": t["metrics"]["m5_maximum_drawdown"],
                      "ranks": ranks[tid]})
    # winner = lowest avg rank; ties -> higher return, then lower MDD
    board.sort(key=lambda r: (r["avg_rank"], -r["m1_cumulative_return"], r["m5_maximum_drawdown"]))
    (results_dir / "leaderboard.json").write_text(json.dumps(board, indent=2, default=str))
    return board
