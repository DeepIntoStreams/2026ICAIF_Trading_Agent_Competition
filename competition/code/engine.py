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

Live model (agreed with deployment): the server runs ONE batch per day AFTER the market close.
It fetches that day's bar, calls `execute_decision` to fill the pending decision at the OPEN and
mark NAV at the CLOSE in a single pass (no intraday / real-time data), persists the full day
record via `db_adapter`, then publishes the next observation. A decision submitted between one
close and the next open executes at that next open (submission window: prior 5PM ET -> 9AM ET).

Two-step decision boundary (matches the deployment contract): validation happens at SUBMISSION
time via `validate_decision` (reject-not-repair) and the *accepted* vector is stored; the fill
happens later via `execute_decision(..., pre_validated=True)`, which does NOT re-validate. All
money / quantity / weight arithmetic uses `Decimal` (-> PostgreSQL NUMERIC(28,12)) so the persisted
numbers are exact; statistical metrics (Sharpe, Sortino, VaR, ...) stay float.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal, ROUND_HALF_EVEN, getcontext
from pathlib import Path
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent                 # competition/code
sys.path.insert(0, str(HERE))                          # sibling framework modules
sys.path.insert(0, str(HERE.parents[1] / "src"))       # portfolio_agent (repo/src)

from runner import code_hash, run_agent                # competition/code/runner.py
from portfolio_agent.metrics import compute_metrics
from portfolio_agent.risk import validate_target_weights

getcontext().prec = 34                                 # headroom above NUMERIC(28, 12)
_Q12 = Decimal("0.000000000001")                       # 12 decimal places = the stored precision
_TINY = Decimal("1e-9")

FEE_RATE = Decimal("0.001")
INITIAL_CAPITAL = Decimal("1000000")
PANEL_KEYS = ("session_date", "event_time_utc", "assets", "market_features",
              "fundamental_features", "constraints", "news")


def _D(x: Any) -> Decimal:
    """Coerce to Decimal via str() so a float's binary noise never enters the accounting."""
    return x if isinstance(x, Decimal) else Decimal(str(x))


def _q(x: Any) -> Decimal:
    """Quantize to NUMERIC(28, 12) precision - what the database actually stores."""
    return _D(x).quantize(_Q12, rounding=ROUND_HALF_EVEN)


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


def validate_decision(raw_weights: Any, instruments: list[Any],
                      constraints: dict[str, Any]) -> dict[str, Any]:
    """SUBMISSION-time validator (the deployment's `weight_validator`): reject-not-repair.

    Run this when the team POSTs, so they get an immediate accept/reject. On any constraint
    violation the whole decision is REJECTED (no clipping); the accepted vector is then what
    `execute_decision(..., pre_validated=True)` fills at the open. A rejected decision is held
    (no trade) and counts one violation day toward M9.

      returns: {"accepted": dict | None, "violations": list, "ok": bool}
    """
    cap = float(constraints.get("max_asset_weight", 0.30))
    gross_cap = float(constraints.get("max_gross_exposure", 1.00))
    weights, violations = validate_target_weights(raw_weights or {}, instruments, cap, gross_cap)
    ok = not violations
    return {"accepted": weights if ok else None, "violations": violations, "ok": ok}


def settle_open(state: dict[str, Any], target_weights: Any, open_prices: dict[str, Any],
                instruments: list[Any], constraints: dict[str, Any],
                fee_rate: Any = FEE_RATE, submitted: bool = True,
                pre_validated: bool = False) -> dict[str, Any]:
    """Phase 1 of the daily batch - fill the pending decision at the recorded OPEN (Decimal).

    KEY-AGNOSTIC / id-first: the keys of `state["shares"]`, `open_prices`, `target_weights` and
    `instruments` may be `instrument_id` ints (preferred - stable across ticker renames) or ticker
    strings; the engine only matches keys, it never interprets them. Records carry that key back
    under `"instrument"`.

    Validation modes:
      pre_validated=False (default; backtest / replay convenience): validate here WITHOUT repair
        (reject-not-repair) - an invalid decision is rejected and no trade happens.
      pre_validated=True (live): `target_weights` is the already-accepted vector (or None to hold);
        do NOT validate again. This is the deployment's execution boundary - the accepted weight
        vector was frozen at submission time and stored.

    A rejected / missing / held decision leaves holdings unchanged (no trade). Emits the full
    per-event record needed to persist the day: individual trades, the open-execution summary,
    and the post-open portfolio snapshot.

      state: {"cash": Decimal, "shares": {ticker: qty}, "peak_nav": Decimal}
      returns: {new_state, transaction, weights, violations, executed,
                transactions[], execution, open_snapshot}
    """
    cap = float(constraints.get("max_asset_weight", 0.30))
    gross_cap = float(constraints.get("max_gross_exposure", 1.00))
    fee_rate = _D(fee_rate)
    cash_before = _D(state["cash"])
    shares = {t: _D(s) for t, s in state.get("shares", {}).items()}     # holdings before the open
    px = {t: _D(p) for t, p in open_prices.items()}

    if pre_validated:
        weights = target_weights                                       # accepted vector, or None
        violations: list[Any] = []
    elif submitted:
        weights, violations = validate_target_weights(target_weights or {}, instruments, cap, gross_cap)
        if violations:
            weights = None                                             # rejected -> no trade
    else:
        weights, violations = None, ["no_submission"]

    # NAV at the open (current holdings valued at the open) - always defined, even on no-trade.
    nav_open = cash_before + sum((sh * px[t] for t, sh in shares.items() if t in px), _D(0))

    traded = _D(0)
    fee = _D(0)
    cash_after = cash_before
    new_shares = dict(shares)
    if weights is not None:
        w = {t: _D(v) for t, v in weights.items()}
        for t in set(shares) | set(w):
            o = px.get(t)
            if o is None or o <= 0:
                continue                                               # untradeable today -> hold
            new_shares[t] = w.get(t, _D(0)) * nav_open / o             # absent target -> 0 (sell)
        traded = sum((abs(new_shares.get(t, _D(0)) - shares.get(t, _D(0))) * px[t]
                      for t in (set(shares) | set(new_shares)) if t in px), _D(0))
        fee = traded * fee_rate
        invested = sum((sh * px[t] for t, sh in new_shares.items() if t in px), _D(0))
        cash_after = nav_open - invested - fee
        if cash_after < 0:
            # Infeasible: the fill would overdraw cash (a fully-invested book can't cover the fee).
            # Reject the whole fill and HOLD - reject-not-repair, so weights are never rescaled. This
            # is an EXECUTION-time check (the fee depends on turnover, unknown at receipt). It leaves
            # `violations` empty, so it does NOT count toward M9; it just holds like a no-trade day.
            weights, traded, fee, cash_after = None, _D(0), _D(0), cash_before
            new_shares = dict(shares)
        else:
            new_shares = {t: sh for t, sh in new_shares.items() if abs(sh) > _D("1e-12")}

    # Per-instrument trades (shares_before -> new_shares); fee split pro-rata by notional so the
    # individual fees reconcile to the total, and the cash_change values sum to the cash delta.
    transactions: list[dict[str, Any]] = []
    total_buy = _D(0)
    total_sell = _D(0)
    for t in sorted(set(shares) | set(new_shares)):
        o = px.get(t)
        if o is None or o <= 0:
            continue
        before, after = shares.get(t, _D(0)), new_shares.get(t, _D(0))
        dq = after - before
        if abs(dq) < _TINY:
            continue
        gross = abs(dq) * o
        fee_t = (gross / traded * fee) if traded > 0 else _D(0)
        if dq > 0:
            side, cash_change = "BUY", -(gross + fee_t)
            total_buy += gross
        else:
            side, cash_change = "SELL", (gross - fee_t)
            total_sell += gross
        transactions.append({"instrument": t, "side": side, "shares_before": _q(before),
                             "target_shares": _q(after), "quantity": _q(abs(dq)), "price": _q(o),
                             "gross_amount": _q(gross), "fee": _q(fee_t), "cash_change": _q(cash_change)})

    positions_value_open = sum((sh * px[t] for t, sh in new_shares.items() if t in px), _D(0))
    nav_post_open = cash_after + positions_value_open
    open_positions = [{"instrument": t, "quantity": _q(sh), "reference_price": _q(px[t]),
                       "market_value": _q(sh * px[t]),
                       "weight": _q(sh * px[t] / nav_post_open) if nav_post_open > 0 else _q(0)}
                      for t, sh in sorted(new_shares.items()) if t in px]

    return {
        "new_state": {"cash": _q(cash_after), "shares": {t: _q(s) for t, s in new_shares.items()},
                      "peak_nav": _q(state.get("peak_nav", 0))},
        "transaction": {"traded_notional": _q(traded), "fee": _q(fee)},   # unchanged summary
        "weights": weights, "violations": violations, "executed": weights is not None,
        "transactions": transactions,
        "execution": {"nav_open": _q(nav_open), "cash_before": _q(cash_before),
                      "cash_after": _q(cash_after), "total_buy_value": _q(total_buy),
                      "total_sell_value": _q(total_sell), "total_fee": _q(fee)},
        "open_snapshot": {"cash": _q(cash_after), "positions_value": _q(positions_value_open),
                          "nav": _q(nav_post_open),
                          "gross_exposure": _q(positions_value_open / nav_post_open)
                          if nav_post_open > 0 else _q(0),
                          "positions": open_positions},
    }


def mark_to_close(state: dict[str, Any], close_prices: dict[str, Any]) -> dict[str, Any]:
    """Phase 2 of the daily batch - value the portfolio at the recorded CLOSE (Decimal). No trading.

    Value the portfolio at the close, update the running peak, compute drawdown. Emits the close
    portfolio snapshot with the per-position breakdown needed to persist the day.
      returns: {new_state, nav_close, positions_value, drawdown, close_snapshot}
    """
    cash = _D(state["cash"])
    shares = {t: _D(s) for t, s in state.get("shares", {}).items()}
    peak_nav = _D(state.get("peak_nav", 0))
    px = {t: _D(p) for t, p in close_prices.items()}
    positions = [{"instrument": t, "quantity": _q(sh), "reference_price": _q(px[t]),
                  "market_value": _q(sh * px[t])}
                 for t, sh in sorted(shares.items()) if t in px]
    positions_value = sum((_D(p["market_value"]) for p in positions), _D(0))
    nav_close = cash + positions_value
    peak_nav = max(peak_nav, nav_close)
    drawdown = max(_D(0), (peak_nav - nav_close) / peak_nav) if peak_nav > 0 else _D(0)
    for p in positions:
        p["weight"] = _q(_D(p["market_value"]) / nav_close) if nav_close > 0 else _q(0)
    return {"new_state": {"cash": _q(cash), "shares": {t: _q(s) for t, s in shares.items()},
                          "peak_nav": _q(peak_nav)},
            "nav_close": _q(nav_close), "positions_value": _q(positions_value), "drawdown": _q(drawdown),
            "close_snapshot": {"cash": _q(cash), "positions_value": _q(positions_value),
                               "nav": _q(nav_close),
                               "gross_exposure": _q(positions_value / nav_close) if nav_close > 0 else _q(0),
                               "drawdown": _q(drawdown), "positions": positions}}


def execute_decision(state: dict[str, Any], target_weights: Any,
                     open_prices: dict[str, Any], close_prices: dict[str, Any],
                     instruments: list[Any], constraints: dict[str, Any],
                     fee_rate: Any = FEE_RATE, submitted: bool = True,
                     prev_close_nav: Any = None, initial_capital: Any = INITIAL_CAPITAL,
                     pre_validated: bool = False) -> dict[str, Any]:
    """THE daily batch, run once after the market close (the agreed live model).

    Given the full day bar (open + close), fill the pending decision at the OPEN and mark NAV at
    the CLOSE in a single pass - so the server fetches each day's data once, after the close, with
    no intraday / real-time processing. Same economics as `settle_open` then `mark_to_close`; also
    used for offline replay / validation. Pass `pre_validated=True` with the accepted vector (or
    None to hold) when validation already happened at submission time.

    Returns the plain summary keys the offline driver reads PLUS a complete, persistable record of
    the day - `decision`, `transactions`, `execution`, `open_snapshot`, `close_snapshot`,
    `performance` - one block per table in the live database (see competition/code/db_adapter.py).
    """
    o = settle_open(state, target_weights, open_prices, instruments, constraints, fee_rate,
                    submitted, pre_validated)
    c = mark_to_close(o["new_state"], close_prices)
    nav_close = c["nav_close"]
    nav_open = o["execution"]["nav_open"]
    init = _D(initial_capital)
    prev = _D(prev_close_nav) if prev_close_nav is not None else None
    daily_return = _q(nav_close / prev - 1) if (prev is not None and prev > 0) else None
    cumulative_return = _q(nav_close / init - 1) if init > 0 else None
    turnover = _q(o["transaction"]["traded_notional"] / nav_open) if nav_open > 0 else _q(0)
    return {
        # --- unchanged summary keys (offline driver / metrics) ---
        "new_state": c["new_state"], "transaction": o["transaction"],
        "nav_close": nav_close, "drawdown": c["drawdown"], "weights": o["weights"],
        "violations": o["violations"], "executed": o["executed"],
        # --- full persistable day record (for the live database) ---
        "decision": {"raw": dict(target_weights or {}), "accepted": dict(o["weights"] or {}),
                     "submitted": submitted, "executed": o["executed"], "violations": o["violations"]},
        "transactions": o["transactions"], "execution": o["execution"],
        "open_snapshot": o["open_snapshot"], "close_snapshot": c["close_snapshot"],
        "performance": {"previous_close_nav": _q(prev) if prev is not None else None,
                        "current_close_nav": nav_close, "daily_return": daily_return,
                        "cumulative_return": cumulative_return, "turnover": turnover,
                        "transaction_cost": o["execution"]["total_fee"], "drawdown": c["drawdown"]},
    }


def build_observation(panel: dict[str, Any],
                      close_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """Attach a team's close-portfolio state to the shared panel -> the participant's observation
    (the deployment's `observation_builder`).

    `close_snapshot` is what `mark_to_close` / `execute_decision` returned for the day just
    finalized (cash, nav, per-position weights); the portfolio block below is exactly what the
    team sees as its state entering the next decision. The payload is float (JSON to the
    participant); the accounting stays Decimal upstream. This is the single implementation the
    live path and the backtest both use, so the served observation can't drift between them.
    """
    obs = dict(panel)
    if close_snapshot is not None:
        nav = _D(close_snapshot["nav"])
        cash = _D(close_snapshot["cash"])
        obs["portfolio"] = {
            "cash": float(cash), "nav": float(nav),
            "cash_ratio": float(cash / nav) if nav > 0 else 1.0,
            "drawdown": float(_D(close_snapshot.get("drawdown", 0))),
            "weights": {p["instrument"]: float(_D(p["weight"]))
                        for p in close_snapshot.get("positions", [])},
        }
    return obs


def advance_day(state: dict[str, Any], target_weights: Any,
                open_prices: dict[str, Any], close_prices: dict[str, Any],
                instruments: list[Any], constraints: dict[str, Any], *,
                observation_panel: dict[str, Any] | None = None,
                fee_rate: Any = FEE_RATE, submitted: bool = True,
                prev_close_nav: Any = None, initial_capital: Any = INITIAL_CAPITAL,
                pre_validated: bool = False) -> dict[str, Any]:
    """Atomic finalize-then-observe - the ordering guarantee for the live loop.

    Runs the after-close batch (`execute_decision`) AND builds the next observation from the
    just-finalized close portfolio, in ONE call. The next observation therefore cannot be
    published against stale state: it does not exist until the day is finalized, so a participant
    can never submit against a portfolio the server hasn't closed out yet. `observation_panel` is
    the shared panel for the observation to publish (built from the finalized day's data); pass
    None on the final day.

      returns: {record, new_state, next_observation}
    """
    rec = execute_decision(state, target_weights, open_prices, close_prices, instruments, constraints,
                           fee_rate=fee_rate, submitted=submitted, prev_close_nav=prev_close_nav,
                           initial_capital=initial_capital, pre_validated=pre_validated)
    next_obs = (build_observation(observation_panel, rec["close_snapshot"])
                if observation_panel is not None else None)
    return {"record": rec, "new_state": rec["new_state"], "next_observation": next_obs}


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
    at that day's open under the 0.1% fee. State/accounting is Decimal; the observation payload and
    the metric inputs are converted to float at the boundary. `observation()` has no side effects;
    `submit()` advances to the next day.
    """

    def __init__(self, dates: list[str], panels: dict[str, Any], market: dict[str, Any],
                 tickers: list[str], cap: float, gross_cap: float,
                 initial_capital: Any = INITIAL_CAPITAL, fee_rate: Any = FEE_RATE):
        self.dates, self.panels, self.market, self.tickers = dates, panels, market, tickers
        self.cap, self.gross_cap = cap, gross_cap
        self.initial_capital, self.fee_rate = _D(initial_capital), _D(fee_rate)
        self.i = 0
        self.cash = self.initial_capital
        self.peak_nav = self.initial_capital
        self.shares: dict[str, Decimal] = {}
        self.nav_series: list[Decimal] = []
        self.total_cost = self.total_traded = _D(0)
        self.violation_days = 0
        self.day_records: list[dict[str, Any]] = []            # full per-day records (persistable)

    def _op(self, d, t):
        v = self.market[d].get(t, {}).get("adj_open")
        return _D(v) if v is not None else None

    def _cl(self, d, t):
        v = self.market[d].get(t, {}).get("adj_close")
        return _D(v) if v is not None else None

    @property
    def done(self) -> bool:
        return self.i >= len(self.dates)

    @property
    def current_date(self) -> str | None:
        return None if self.done else self.dates[self.i]

    def observation(self) -> dict[str, Any]:
        """The official observation for the current trading day: the shared panel plus this team's
        portfolio state valued at the previous close. No side effects (portfolio block is float)."""
        if self.done:
            raise RuntimeError("episode is finished")
        d = self.dates[self.i]
        prev_d = self.dates[self.i - 1] if self.i > 0 else None
        if prev_d is None:
            state_nav, state_weights, cash_ratio = self.initial_capital, {}, 1.0
        else:
            pcpx = {t: self._cl(prev_d, t) for t in self.shares if self._cl(prev_d, t) is not None}
            state_nav = self.cash + sum((sh * pcpx[t] for t, sh in self.shares.items() if t in pcpx), _D(0))
            state_weights = {t: float(sh * pcpx[t] / state_nav)
                             for t, sh in self.shares.items() if t in pcpx and state_nav > 0}
            cash_ratio = float(self.cash / state_nav) if state_nav > 0 else 1.0
        peak = self.peak_nav if self.peak_nav > 0 else state_nav
        drawdown = max(_D(0), (peak - state_nav) / peak) if peak > 0 else _D(0)
        obs = dict(self.panels[d])
        obs["portfolio"] = {"weights": state_weights, "cash_ratio": cash_ratio,
                            "nav": float(state_nav), "drawdown": float(drawdown)}
        return obs

    def submit(self, raw_weights: Any, submitted: bool = True) -> dict[str, Any]:
        """Validate WITHOUT repair, execute a valid decision at today's open, mark NAV at today's
        close, and advance. `submitted=False` = no decision arrived (no trade, not counted toward
        M9). Returns the per-day verdict; the full persistable record is appended to
        `self.day_records`."""
        if self.done:
            raise RuntimeError("episode is finished")
        d = self.dates[self.i]
        open_px = {t: self._op(d, t) for t in self.tickers if self._op(d, t) is not None}
        close_px = {t: self._cl(d, t) for t in self.tickers if self._cl(d, t) is not None}
        prev_close_nav = self.nav_series[-1] if self.nav_series else self.initial_capital
        r = execute_decision(
            {"cash": self.cash, "shares": self.shares, "peak_nav": self.peak_nav},
            raw_weights, open_px, close_px, self.tickers,
            {"max_asset_weight": self.cap, "max_gross_exposure": self.gross_cap},
            fee_rate=self.fee_rate, submitted=submitted,
            prev_close_nav=prev_close_nav, initial_capital=self.initial_capital)
        self.cash = r["new_state"]["cash"]
        self.shares = r["new_state"]["shares"]
        self.peak_nav = r["new_state"]["peak_nav"]
        self.total_cost += _D(r["transaction"]["fee"])
        self.total_traded += _D(r["transaction"]["traded_notional"])
        if submitted and r["violations"]:                      # submitted-invalid -> M9 (no-show not)
            self.violation_days += 1
        self.nav_series.append(r["nav_close"])
        self.day_records.append({"session_date": d, **{k: r[k] for k in (
            "decision", "transactions", "execution", "open_snapshot", "close_snapshot", "performance")}})
        self.i += 1
        return {"session_date": d, "executed": r["executed"],
                "target_weights": r["weights"] or {}, "violations": r["violations"]}

    def result(self) -> dict[str, Any]:
        """M1-M9 for the completed episode (metrics computed in float from the NAV series).

        The equity curve starts at the INITIAL CAPITAL: `compute_metrics` measures cumulative
        return as values[-1]/values[0], so seeding the baseline makes M1 the true return from the
        starting capital and lets day 1's return count toward the risk metrics. Without it the
        curve began at day 1's close and that first day was silently dropped. This matches the
        live board (`trading_repo.compute_and_store_leaderboard`), so validation and official
        scoring are computed the same way.
        """
        return compute_metrics(
            [float(self.initial_capital)] + [float(v) for v in self.nav_series],
            initial_capital=float(self.initial_capital),
            total_transaction_cost=float(self.total_cost), total_trade_value=float(self.total_traded),
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
               "final_nav": float(ep.nav_series[-1]),
               "dates": dates, "nav_series": [round(float(v), 2) for v in ep.nav_series]}
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
    board = rank_board(teams, method=method)
    if board:
        (results_dir / "leaderboard.json").write_text(json.dumps(board, indent=2, default=str))
    return board


def rank_board(teams: list[dict[str, Any]], method: str = "dimension") -> list[dict[str, Any]]:
    """Pure ranking step: [{team_id, metrics}, ...] -> the ranked board (lower avg_rank wins).

    Shared by the file-based `leaderboard()` (offline replay) and the database leaderboard
    (`trading_repo.compute_and_store_leaderboard`), so the live board and the backtest board are
    ranked by exactly the same rule. Adds an explicit 1-based `rank` to each entry.
    """
    if not teams:
        return []
    ranks: dict[Any, dict[str, int]] = {t["team_id"]: {} for t in teams}
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
                      "metrics": t["metrics"],
                      "m1_cumulative_return": t["metrics"].get("m1_cumulative_return"),
                      "m5_maximum_drawdown": t["metrics"].get("m5_maximum_drawdown"),
                      "dimension_scores": {d: round(s, 4) for d, s in dim_scores.items()},
                      "ranks": r})
    board.sort(key=lambda x: (x["avg_rank"], -(x["m1_cumulative_return"] or 0.0),
                              (x["m5_maximum_drawdown"] or 0.0), x["team_id"]))
    for position, entry in enumerate(board, 1):
        entry["rank"] = position
    return board
