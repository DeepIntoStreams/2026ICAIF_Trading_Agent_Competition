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
from portfolio_agent.risk import validate_target_weights

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


def settle_open(state: dict[str, Any], target_weights: Any, open_prices: dict[str, float],
                tickers: list[str], constraints: dict[str, Any],
                fee_rate: float = FEE_RATE, submitted: bool = True) -> dict[str, Any]:
    """LIVE STEP 1 - at the 9:30 open (needs ONLY the open prices).

    Validate WITHOUT repair (reject-not-repair) and fill a valid decision at the open. A
    rejected or missing decision leaves holdings unchanged (no trade). Returns the new holdings
    and the executed transaction. NAV is NOT computed here - that waits for the close.

      state: {"cash": float, "shares": {ticker: qty}, "peak_nav": float}
      returns: {new_state, transaction, weights, violations, executed}
    """
    cap = float(constraints.get("max_asset_weight", 0.30))
    gross_cap = float(constraints.get("max_gross_exposure", 1.00))
    cash = float(state["cash"])
    shares = dict(state.get("shares", {}))

    if submitted:
        weights, violations = validate_target_weights(target_weights or {}, tickers, cap, gross_cap)
        if violations:
            weights = None                                     # rejected -> no trade
    else:
        weights, violations = None, ["no_submission"]

    traded = fee = 0.0
    if weights is not None:
        nav_open = cash + sum(sh * open_prices[t] for t, sh in shares.items() if t in open_prices)
        new_shares = dict(shares)
        for t in set(shares) | set(weights):
            o = open_prices.get(t)
            if not o or o <= 0:
                continue                                       # untradeable today -> keep holding
            new_shares[t] = weights.get(t, 0.0) * nav_open / o   # absent target -> 0 (sell)
        traded = sum(abs(new_shares.get(t, 0.0) - shares.get(t, 0.0)) * open_prices[t]
                     for t in (set(shares) | set(new_shares)) if t in open_prices)
        fee = traded * fee_rate
        invested = sum(sh * open_prices[t] for t, sh in new_shares.items() if t in open_prices)
        cash = nav_open - invested - fee
        shares = {t: sh for t, sh in new_shares.items() if abs(sh) > 1e-12}

    return {"new_state": {"cash": cash, "shares": shares, "peak_nav": float(state.get("peak_nav", 0.0))},
            "transaction": {"traded_notional": traded, "fee": fee},
            "weights": weights, "violations": violations, "executed": weights is not None}


def mark_to_close(state: dict[str, Any], close_prices: dict[str, float]) -> dict[str, Any]:
    """LIVE STEP 2 - after the 4:00 close (needs ONLY the close prices). No trading.

    Value the portfolio at the close, update the running peak, and compute drawdown.
      returns: {new_state, nav_close, positions_value, drawdown}
    """
    cash = float(state["cash"])
    shares = dict(state.get("shares", {}))
    peak_nav = float(state.get("peak_nav", 0.0))
    positions_value = sum(sh * close_prices[t] for t, sh in shares.items() if t in close_prices)
    nav_close = cash + positions_value
    peak_nav = max(peak_nav, nav_close)
    drawdown = max(0.0, (peak_nav - nav_close) / peak_nav) if peak_nav > 0 else 0.0
    return {"new_state": {"cash": cash, "shares": shares, "peak_nav": peak_nav},
            "nav_close": nav_close, "positions_value": positions_value, "drawdown": drawdown}


def execute_decision(state: dict[str, Any], target_weights: Any,
                     open_prices: dict[str, float], close_prices: dict[str, float],
                     tickers: list[str], constraints: dict[str, Any],
                     fee_rate: float = FEE_RATE, submitted: bool = True) -> dict[str, Any]:
    """Combined open-fill + close-mark, for REPLAY / VALIDATION where the full bar is known at
    once. Live servers call `settle_open` at 9:30 and `mark_to_close` at 16:00 instead.

      returns: {new_state, transaction, nav_close, drawdown, weights, violations, executed}
    """
    o = settle_open(state, target_weights, open_prices, tickers, constraints, fee_rate, submitted)
    c = mark_to_close(o["new_state"], close_prices)
    return {"new_state": c["new_state"], "transaction": o["transaction"],
            "nav_close": c["nav_close"], "drawdown": c["drawdown"], "weights": o["weights"],
            "violations": o["violations"], "executed": o["executed"]}


class TradingEpisode:
    """Step-wise driver for ONE team through an N-day episode, one round-trip at a time.

    This is the primitive a live/validation server wraps. It hands out one observation, takes one
    decision, executes it, and advances - the same sequential observation -> decision -> execute
    loop the participant experiences, whether the server drives it fast (Validation) or one real
    trading day at a time (Official Competition):

        ep = TradingEpisode(dates, panels, market, tickers, cap, gross_cap)
        while not ep.done:
            obs = ep.observation()      # send this (incl. portfolio state) to the participant
            weights = ...               # receive their decision_response target_weights
            ep.submit(weights)          # validate (reject-not-repair), fill at the open, advance
        summary = ep.result()           # M1-M9 for the episode

    Semantics are identical to the backtester (authoritative model): the observation is
    point-in-time (market/fundamentals through the PREVIOUS close, NO current-day price; portfolio
    state valued at the previous close); a submitted-invalid decision is REJECTED and counts one
    violation day toward M9; a missing decision is no-trade (NOT counted); a valid decision fills
    at that day's open under the 0.1% fee. `observation()` has no side effects and may be called
    repeatedly for the current day; `submit()` advances to the next day.
    """

    def __init__(self, dates: list[str], panels: dict[str, Any], market: dict[str, Any],
                 tickers: list[str], cap: float, gross_cap: float,
                 initial_capital: float = INITIAL_CAPITAL, fee_rate: float = FEE_RATE):
        self.dates, self.panels, self.market, self.tickers = dates, panels, market, tickers
        self.cap, self.gross_cap = cap, gross_cap
        self.initial_capital, self.fee_rate = initial_capital, fee_rate
        self.i = 0
        self.cash = initial_capital
        self.peak_nav = initial_capital
        self.shares: dict[str, float] = {}
        self.nav_series: list[float] = []
        self.total_cost = self.total_traded = 0.0
        self.violation_days = 0

    def _op(self, d, t): return self.market[d].get(t, {}).get("adj_open")
    def _cl(self, d, t): return self.market[d].get(t, {}).get("adj_close")

    @property
    def done(self) -> bool:
        return self.i >= len(self.dates)

    @property
    def current_date(self) -> str | None:
        return None if self.done else self.dates[self.i]

    def observation(self) -> dict[str, Any]:
        """The official observation for the current trading day: the shared panel plus this team's
        portfolio state valued at the previous close. No side effects."""
        if self.done:
            raise RuntimeError("episode is finished")
        d = self.dates[self.i]
        prev_d = self.dates[self.i - 1] if self.i > 0 else None
        if prev_d is None:
            state_nav, state_weights, cash_ratio = self.initial_capital, {}, 1.0
        else:
            pcpx = {t: self._cl(prev_d, t) for t in self.shares if self._cl(prev_d, t)}
            state_nav = self.cash + sum(sh * pcpx[t] for t, sh in self.shares.items() if t in pcpx)
            state_weights = _weights_from_shares(self.shares, pcpx, state_nav)
            cash_ratio = (self.cash / state_nav) if state_nav > 0 else 1.0
        peak = self.peak_nav if self.peak_nav > 0 else state_nav
        drawdown = max(0.0, (peak - state_nav) / peak) if peak > 0 else 0.0
        obs = dict(self.panels[d])
        obs["portfolio"] = {"weights": state_weights, "cash_ratio": cash_ratio,
                            "nav": state_nav, "drawdown": drawdown}
        return obs

    def submit(self, raw_weights: Any, submitted: bool = True) -> dict[str, Any]:
        """Validate WITHOUT repair, execute a valid decision at today's open, mark NAV at today's
        close, and advance. `submitted=False` = no decision arrived (no trade, not counted toward
        M9). Returns the per-day server verdict."""
        if self.done:
            raise RuntimeError("episode is finished")
        d = self.dates[self.i]
        open_px = {t: self._op(d, t) for t in self.tickers if self._op(d, t)}
        close_px = {t: self._cl(d, t) for t in self.tickers if self._cl(d, t)}
        r = execute_decision(
            {"cash": self.cash, "shares": self.shares, "peak_nav": self.peak_nav},
            raw_weights, open_px, close_px, self.tickers,
            {"max_asset_weight": self.cap, "max_gross_exposure": self.gross_cap},
            fee_rate=self.fee_rate, submitted=submitted)
        self.cash = r["new_state"]["cash"]
        self.shares = r["new_state"]["shares"]
        self.peak_nav = r["new_state"]["peak_nav"]
        self.total_cost += r["transaction"]["fee"]
        self.total_traded += r["transaction"]["traded_notional"]
        if submitted and r["violations"]:                      # submitted-invalid -> M9 (no-show not)
            self.violation_days += 1
        self.nav_series.append(r["nav_close"])
        self.i += 1
        return {"session_date": d, "executed": r["executed"],
                "target_weights": r["weights"] or {}, "violations": r["violations"]}

    def result(self) -> dict[str, Any]:
        """M1-M9 for the completed episode."""
        return compute_metrics(
            self.nav_series, initial_capital=self.initial_capital,
            total_transaction_cost=self.total_cost, total_trade_value=self.total_traded,
            violation_steps=self.violation_days, decision_steps=len(self.dates))


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

    # Drive the episode ONE ROUND-TRIP AT A TIME - the same primitive the live/validation server
    # wraps (send observation -> receive decision -> execute -> advance).
    ep = TradingEpisode(dates, panels, market, tickers, cap, gross_cap)
    while not ep.done:
        d = ep.current_date

        # 1) SEND: the observation + this team's portfolio state, recorded for reproducibility
        observation = ep.observation()
        request = {"type": "decision_request", "protocol_version": "0.1",
                   "team_id": team_id, "session_date": d,
                   "deadline_utc": observation.get("event_time_utc"),
                   "observation": observation}
        (req_dir / f"{d}.json").write_text(json.dumps(request))

        # 2) RECEIVE the decision: POSTed (live intake) or by running the team's code (sim/audit)
        submitted, raw = False, {}
        if posted_dir is not None:                         # LIVE INTAKE: read what the team POSTED
            post_path = Path(posted_dir) / team_id / f"{d}.json"
            if post_path.exists():
                post = json.loads(post_path.read_text())
                raw, submitted = post.get("target_weights", {}) or {}, True
                audit = {"source": "posted", "post_path": str(post_path),
                         "agent_version": post.get("metadata", {}).get("agent_version")}
            else:
                audit = {"source": "posted", "reason": "no_submission"}
        else:                                              # SIMULATE / AUDIT: run their code
            res = run_agent(code_dir, observation, panels[d]["assets"], constraints, timeout_seconds)
            audit = res["audit"]
            if res["ok"]:
                raw, submitted = res["target_weights"], True
            else:
                audit["no_submission"] = True

        # 3) VALIDATE (reject-not-repair), EXECUTE at the open, ADVANCE
        verdict = ep.submit(raw, submitted)

        # 4) RECORD the server-side ledger entry (the reproducible trail)
        (sub_dir / f"{d}.json").write_text(json.dumps({
            "type": "decision_response", "protocol_version": "0.1",
            "team_id": team_id, "session_date": d,
            "target_weights": verdict["target_weights"],
            "server": {"submitted": submitted, "executed": verdict["executed"],
                       "violations": verdict["violations"], "audit": audit},
        }, indent=2))

    summary = {"team_id": team_id,
               "code_hash": code_hash(code_dir) if code_dir else "posted",
               "source": "posted" if posted_dir else "code",
               "n_days": len(dates), "metrics": ep.result(),
               "final_nav": ep.nav_series[-1],
               "dates": dates, "nav_series": [round(v, 2) for v in ep.nav_series]}
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


# the four evaluation dimensions (each weighted equally under the two-level ranking)
_DIMENSIONS = {
    "profitability": ["m1_cumulative_return", "m2_daily_win_rate"],
    "risk_adjusted": ["m3_sharpe_ratio", "m4_sortino_ratio"],
    "risk_management": ["m5_maximum_drawdown", "m6_value_at_risk_95", "m7_expected_shortfall_95"],
    "execution": ["m8_turnover", "m9_violation_rate"],
}


def leaderboard(out_dir: str, method: str = "dimension") -> list[dict[str, Any]]:
    """Average-rank leaderboard; lower `avg_rank` wins.

      method="dimension" (default; matches the Evaluation page): rank teams under each metric,
        average the ranks WITHIN each of the four dimensions, then average the four dimension
        scores - so every dimension carries equal weight (1/4).
      method="flat" (2025 style): the plain average of the nine metric ranks (each metric 1/9).

    Ties break by higher cumulative return (M1), then lower max drawdown (M5), then team_id
    (a deterministic stand-in for registration order, which the engine does not track).
    """
    results_dir = Path(out_dir) / "results"
    teams = [json.loads(p.read_text()) for p in results_dir.glob("*.json")
             if p.name != "leaderboard.json"]     # never re-ingest our own output
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
        r = ranks[tid]
        dim_scores = {dim: sum(r[k] for k in keys) / len(keys)
                      for dim, keys in _DIMENSIONS.items()}
        if method == "flat":
            avg = sum(r.values()) / len(_HIGHER_BETTER)
        else:
            avg = sum(dim_scores.values()) / len(_DIMENSIONS)
        board.append({"team_id": tid, "avg_rank": round(avg, 4), "method": method,
                      "m1_cumulative_return": t["metrics"]["m1_cumulative_return"],
                      "m5_maximum_drawdown": t["metrics"]["m5_maximum_drawdown"],
                      "dimension_scores": {d: round(s, 4) for d, s in dim_scores.items()},
                      "ranks": r})
    board.sort(key=lambda x: (x["avg_rank"], -x["m1_cumulative_return"],
                              x["m5_maximum_drawdown"], x["team_id"]))
    (results_dir / "leaderboard.json").write_text(json.dumps(board, indent=2, default=str))
    return board
