"""Trading persistence layer - loads and saves the trading trajectory to PostgreSQL.

This owns the *trading* side of the database - the tables tightly coupled to the accounting
logic, so their semantics stay with the execution logic that produces them:

    executions, transactions, cash_ledger, portfolio_snapshots, position_snapshots,
    daily_performance, leaderboard

The pure core (`engine.py`) stays DB-agnostic; THIS is the only trading module that touches the
database. The platform tables (teams, instruments, trading_days, decision_submissions,
observations, market_bars, fundamental_records, audit_logs) and all orchestration stay on the
server side. The server resolves the ids, runs the after-close batch, and invokes this repo:

    Trading Core  ->  execution record  ->  TradingRepository  ->  PostgreSQL

Daily use (server side):

    repo  = TradingRepository(conn)
    prior = repo.load_state(team_id, prior_close_snapshot_id=snap_id,
                            execution_day_id=day_id)      # EXPLICIT prior state, never guessed
    rec   = execute_decision(prior["state"], accepted_weights, open_px, close_px,
                             active_instrument_ids, constraints,
                             prev_close_nav=prior["prev_close_nav"], pre_validated=True)
    ids   = repo.persist_day(team_id=team_id, execution_day_id=day_id, submission_id=sub_id,
                             prior_close_snapshot_id=snap_id, record=rec)   # one transaction
    compute_and_store_leaderboard(conn, day_id)           # after the batch

The prior snapshot is resolved by Deployment from the decision itself
(`decision -> observation -> observations.close_portfolio_snapshot_id`) so the execution always
runs against exactly the state the team's observation was built from. This module never falls
back to "the latest CLOSE snapshot"; an invalid or missing reference fails loudly.

`state["shares"]`, `open_px`, `close_px` and `active_instrument_ids` are keyed by `instrument_id`
(the engine is id-first); prices and money are `Decimal`. Nothing here interprets tickers.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db_adapter

INITIAL_CAPITAL = Decimal("1000000")
_PRIOR_REQUIRED = (
    "prior_close_snapshot_id is required: resolve it from the decision's observation "
    "(observations.close_portfolio_snapshot_id). This layer never falls back to the latest "
    "CLOSE snapshot, so the execution always uses the state the team decided against.")


def _num(value: Any) -> Decimal | None:
    """Metric float -> NUMERIC, dropping NaN/inf (an undefined ratio stores as NULL)."""
    if value is None:
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return Decimal(str(number))


class TradingRepository:
    """Load/persist the trading trajectory. `conn` is a psycopg3 Connection (autocommit off)."""

    def __init__(self, conn: Any):
        self.conn = conn

    # ------------------------------------------------------------------ load
    def load_state(self, team_id: int, *, prior_close_snapshot_id: int | None = None,
                   execution_day_id: int | None = None,
                   initial_capital: Decimal = INITIAL_CAPITAL) -> dict[str, Any]:
        """Rebuild the engine portfolio state from an EXPLICIT prior snapshot.

        `prior_close_snapshot_id` is mandatory (a missing one raises rather than silently
        selecting another snapshot). The snapshot is validated: it must exist, belong to this
        team, be an INITIAL or CLOSE snapshot, and precede the execution day. `peak_nav` is the
        max NAV over that snapshot's own ancestor chain (INITIAL included), so drawdown is
        measured against the true high-water mark and never against an unrelated run.

        Returns {"state": {cash, shares:{instrument_id: qty}, peak_nav}, "prev_close_nav",
        "prior_snapshot_id"}; `prev_close_nav` is that snapshot's NAV (the daily-return baseline).
        """
        if prior_close_snapshot_id is None:
            raise ValueError(_PRIOR_REQUIRED)
        row = self.conn.execute(
            """SELECT ps.id, ps.team_id, ps.snapshot_type, ps.nav, ps.cash, td.trading_date
                 FROM portfolio_snapshots ps
                 LEFT JOIN trading_days td ON td.id = ps.trading_day_id
                WHERE ps.id = %s""", (prior_close_snapshot_id,)).fetchone()
        if row is None:
            raise ValueError(f"prior snapshot {prior_close_snapshot_id} does not exist")
        snap_id, snap_team, snap_type, nav, cash, snap_date = row
        if int(snap_team) != int(team_id):
            raise ValueError(
                f"prior snapshot {snap_id} belongs to team {snap_team}, not team {team_id}")
        if snap_type not in ("INITIAL", "CLOSE"):
            raise ValueError(
                f"prior snapshot {snap_id} is {snap_type}; only INITIAL or CLOSE may be resumed")
        if execution_day_id is not None and snap_date is not None:
            exec_date = self.conn.execute(
                "SELECT trading_date FROM trading_days WHERE id = %s",
                (execution_day_id,)).fetchone()
            if exec_date is not None and snap_date >= exec_date[0]:
                raise ValueError(
                    f"prior snapshot {snap_id} ({snap_date}) does not precede the execution day "
                    f"({exec_date[0]})")

        positions = self.conn.execute(
            "SELECT instrument_id, quantity FROM position_snapshots WHERE portfolio_snapshot_id = %s",
            (snap_id,)).fetchall()
        shares = {int(instrument_id): quantity for instrument_id, quantity in positions}
        return {"state": {"cash": cash, "shares": shares, "peak_nav": self.peak_nav(snap_id)},
                "prev_close_nav": nav, "prior_snapshot_id": int(snap_id)}

    def peak_nav(self, snapshot_id: int) -> Decimal:
        """High-water NAV over the snapshot's explicit ancestor chain, back to INITIAL.

        Walking `prior_close_snapshot_id` (rather than taking max over all CLOSE rows) keeps the
        peak tied to this team's actual lineage - it includes the INITIAL capital baseline, and
        it cannot pick up a snapshot from a replay, another run, or a future date.
        """
        return self.conn.execute(
            """WITH RECURSIVE ancestors AS (
                   SELECT id, nav, prior_close_snapshot_id
                     FROM portfolio_snapshots WHERE id = %s
                   UNION
                   SELECT predecessor.id, predecessor.nav, predecessor.prior_close_snapshot_id
                     FROM portfolio_snapshots predecessor
                     JOIN ancestors child ON predecessor.id = child.prior_close_snapshot_id
               )
               SELECT max(nav) FROM ancestors""", (snapshot_id,)).fetchone()[0]

    # --------------------------------------------------------------- persist
    def persist_day(self, *, team_id: int, execution_day_id: int, submission_id: int,
                    record: dict[str, Any], prior_close_snapshot_id: int | None = None,
                    initial_capital: Decimal = INITIAL_CAPITAL,
                    engine_version: str = db_adapter.ENGINE_VERSION,
                    now: datetime | None = None,
                    scheduled_at: datetime | None = None,
                    open_effective_at: datetime | None = None,
                    close_effective_at: datetime | None = None,
                    processed_at: datetime | None = None) -> dict[str, int]:
        """Persist one team-day of the trading trajectory in a single atomic transaction:
        executions -> transactions -> cash_ledger -> portfolio_snapshots (POST_OPEN + CLOSE)
        -> position_snapshots -> daily_performance. Works for a fill or a held/rejected day
        (then transactions and cash_ledger are empty and the execution has zero trades).

        `prior_close_snapshot_id` is mandatory: it is stamped on BOTH generated snapshots, so the
        lineage stays continuous even on a hold day (no trades still advances the chain).

        Audit timestamps - all optional, each defaults to `now`. The batch runs once after the
        close, but the times mean different things:
          open_effective_at  - when the fill economically occurred (the official open); used for
                               the execution, transactions, cash_ledger, and the POST_OPEN snapshot
          close_effective_at - the official close; used for the CLOSE snapshot
          scheduled_at       - executions.scheduled_at (planned open); defaults to open_effective_at
          processed_at       - when the server actually ran the batch; used for processed_at /
                               calculated_at / created_at / updated_at
        """
        if prior_close_snapshot_id is None:
            raise ValueError(_PRIOR_REQUIRED)
        now = now or datetime.now(timezone.utc)
        open_at = open_effective_at or now
        close_at = close_effective_at or now
        sched_at = scheduled_at or open_at
        proc_at = processed_at or now
        e, o, c = record["execution"], record["open_snapshot"], record["close_snapshot"]
        p = record["performance"]
        conn = self.conn
        with conn.transaction():
            exec_id = conn.execute(
                "INSERT INTO executions (team_id, submission_id, trading_day_id, status,"
                " engine_version, scheduled_at, effective_at, processed_at, nav_before, cash_before,"
                " cash_after, total_buy_value, total_sell_value, total_fee, created_at, updated_at)"
                " VALUES (%s,%s,%s,'COMPLETED',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (team_id, submission_id, execution_day_id, engine_version, sched_at, open_at, proc_at,
                 e["nav_open"], e["cash_before"], e["cash_after"], e["total_buy_value"],
                 e["total_sell_value"], e["total_fee"], proc_at, proc_at)).fetchone()[0]

            # insert transactions RETURNING id, keyed by instrument, so each cash-ledger leg links
            # back to its trade (one trade per instrument per execution)
            tx_id_by_instrument: dict[Any, int] = {}
            for tx in record["transactions"]:
                tid = conn.execute(
                    "INSERT INTO transactions (execution_id, instrument_id, side, shares_before,"
                    " target_shares, quantity, price, gross_amount, fee, cash_change, status,"
                    " effective_at, processed_at, created_at)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'COMPLETED',%s,%s,%s) RETURNING id",
                    (exec_id, tx["instrument"], tx["side"], tx["shares_before"], tx["target_shares"],
                     tx["quantity"], tx["price"], tx["gross_amount"], tx["fee"], tx["cash_change"],
                     open_at, proc_at, proc_at)).fetchone()[0]
                tx_id_by_instrument[tx["instrument"]] = tid

            ledger = db_adapter.cash_ledger_rows(record, team_code=team_id,
                                                 execution_date=execution_day_id)
            for cl in ledger:
                conn.execute(
                    "INSERT INTO cash_ledger (team_id, trading_day_id, execution_id, transaction_id,"
                    " event_type, amount, balance_before, balance_after, effective_at, created_at)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (team_id, execution_day_id, exec_id, tx_id_by_instrument.get(cl["instrument"]),
                     cl["event_type"], cl["amount"], cl["balance_before"], cl["balance_after"],
                     open_at, proc_at))
            money_check = Decimal("0.00001")
            if ledger and (
                ledger[-1]["balance_after"].quantize(money_check)
                != e["cash_after"].quantize(money_check)
            ):
                raise ValueError(
                    f"cash_ledger end {ledger[-1]['balance_after']} != cash_after {e['cash_after']}")

            snap_ids: dict[str, int] = {}
            for kind, snap, drawdown, eff in (("POST_OPEN", o, Decimal(0), open_at),
                                              ("CLOSE", c, c["drawdown"], close_at)):
                sid = conn.execute(
                    "INSERT INTO portfolio_snapshots (team_id, trading_day_id, execution_id,"
                    " prior_close_snapshot_id, snapshot_type, cash, positions_value, nav,"
                    " gross_exposure, drawdown, effective_at, created_at)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (team_id, execution_day_id, exec_id, prior_close_snapshot_id, kind,
                     snap["cash"], snap["positions_value"], snap["nav"], snap["gross_exposure"],
                     drawdown, eff, proc_at)).fetchone()[0]
                snap_ids[kind] = sid
                for pos in snap["positions"]:
                    conn.execute(
                        "INSERT INTO position_snapshots (portfolio_snapshot_id, instrument_id,"
                        " quantity, reference_price, market_value, weight, created_at)"
                        " VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (sid, pos["instrument"], pos["quantity"], pos["reference_price"],
                         pos["market_value"], pos["weight"], proc_at))

            prev = p["previous_close_nav"] if p["previous_close_nav"] is not None else initial_capital
            daily_ret = p["daily_return"] if p["daily_return"] is not None else p["cumulative_return"]
            conn.execute(
                "INSERT INTO daily_performance (team_id, trading_day_id, close_portfolio_snapshot_id,"
                " previous_close_nav, current_close_nav, daily_return, cumulative_return,"
                " transaction_cost, turnover, drawdown, calculated_at, created_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (team_id, execution_day_id, snap_ids["CLOSE"], prev, p["current_close_nav"],
                 daily_ret, p["cumulative_return"], p["transaction_cost"], p["turnover"],
                 p["drawdown"], proc_at, proc_at))

        return {"execution_id": exec_id, "post_open_snapshot_id": snap_ids["POST_OPEN"],
                "close_snapshot_id": snap_ids["CLOSE"]}


def compute_and_store_leaderboard(conn: Any, trading_day_id: int, *,
                                  initial_capital: Decimal = INITIAL_CAPITAL,
                                  method: str = "dimension",
                                  now: datetime | None = None) -> list[dict[str, Any]]:
    """Compute the standings through `trading_day_id` and upsert one row per team.

    Called right after the daily batch. M1-M8 come from the trajectory we already persist
    (`daily_performance` NAV series + costs, `executions` traded value); M9 comes from the
    platform's rejected decisions (`decision_submissions.status='REJECTED'`) - a *rejected*
    decision is a violation, while a held day (valid decision, zero trades) is not.

    Ranking is `engine.rank_board`, the same rule the offline replay uses. Rows are upserted on
    (trading_day_id, team_id), so re-running a day overwrites that day's board instead of
    duplicating it. Returns the ranked board.
    """
    from psycopg.types.json import Jsonb

    from engine import rank_board
    from portfolio_agent.metrics import compute_metrics

    now = now or datetime.now(timezone.utc)
    day = conn.execute("SELECT trading_date FROM trading_days WHERE id = %s",
                       (trading_day_id,)).fetchone()
    if day is None:
        raise ValueError(f"trading day {trading_day_id} does not exist")
    as_of = day[0]

    series: dict[int, list[float]] = {}
    costs: dict[int, Decimal] = {}
    for team_id, nav, cost in conn.execute(
            """SELECT dp.team_id, dp.current_close_nav, dp.transaction_cost
                 FROM daily_performance dp
                 JOIN trading_days td ON td.id = dp.trading_day_id
                WHERE td.trading_date <= %s
                ORDER BY dp.team_id, td.trading_date""", (as_of,)).fetchall():
        series.setdefault(int(team_id), []).append(float(nav))
        costs[int(team_id)] = costs.get(int(team_id), Decimal(0)) + (cost or Decimal(0))
    if not series:
        return []

    traded = {int(t): v for t, v in conn.execute(
        """SELECT e.team_id, COALESCE(sum(e.total_buy_value + e.total_sell_value), 0)
             FROM executions e JOIN trading_days td ON td.id = e.trading_day_id
            WHERE td.trading_date <= %s GROUP BY e.team_id""", (as_of,)).fetchall()}
    violations = {int(t): v for t, v in conn.execute(
        """SELECT ds.team_id, count(*) FROM decision_submissions ds
             JOIN trading_days td ON td.id = ds.execution_day_id
            WHERE ds.status = 'REJECTED' AND td.trading_date <= %s
            GROUP BY ds.team_id""", (as_of,)).fetchall()}

    # The equity curve starts at the initial capital: compute_metrics measures cumulative return
    # as values[-1]/values[0], so seeding the baseline makes M1 run from the starting capital
    # (matching the engine's own cumulative_return) and makes day 1 scorable with one close.
    entries = [{"team_id": team_id,
                "metrics": compute_metrics(
                    [float(initial_capital)] + navs,
                    initial_capital=float(initial_capital),
                    total_transaction_cost=float(costs.get(team_id, 0)),
                    total_trade_value=float(traded.get(team_id, 0)),
                    violation_steps=int(violations.get(team_id, 0)),
                    decision_steps=len(navs))}
               for team_id, navs in series.items()]
    board = rank_board(entries, method=method)

    with conn.transaction():
        for entry in board:
            m = entry["metrics"]
            conn.execute(
                """INSERT INTO leaderboard (trading_day_id, team_id, rank, avg_rank,
                       m1_cumulative_return, m2_daily_win_rate, m3_sharpe_ratio, m4_sortino_ratio,
                       m5_maximum_drawdown, m6_value_at_risk_95, m7_expected_shortfall_95,
                       m8_turnover, m9_violation_rate, dimension_scores, calculated_at, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (trading_day_id, team_id) DO UPDATE SET
                       rank = EXCLUDED.rank, avg_rank = EXCLUDED.avg_rank,
                       m1_cumulative_return = EXCLUDED.m1_cumulative_return,
                       m2_daily_win_rate = EXCLUDED.m2_daily_win_rate,
                       m3_sharpe_ratio = EXCLUDED.m3_sharpe_ratio,
                       m4_sortino_ratio = EXCLUDED.m4_sortino_ratio,
                       m5_maximum_drawdown = EXCLUDED.m5_maximum_drawdown,
                       m6_value_at_risk_95 = EXCLUDED.m6_value_at_risk_95,
                       m7_expected_shortfall_95 = EXCLUDED.m7_expected_shortfall_95,
                       m8_turnover = EXCLUDED.m8_turnover,
                       m9_violation_rate = EXCLUDED.m9_violation_rate,
                       dimension_scores = EXCLUDED.dimension_scores,
                       calculated_at = EXCLUDED.calculated_at""",
                (trading_day_id, entry["team_id"], entry["rank"], _num(entry["avg_rank"]),
                 _num(m.get("m1_cumulative_return")), _num(m.get("m2_daily_win_rate")),
                 _num(m.get("m3_sharpe_ratio")), _num(m.get("m4_sortino_ratio")),
                 _num(m.get("m5_maximum_drawdown")), _num(m.get("m6_value_at_risk_95")),
                 _num(m.get("m7_expected_shortfall_95")), _num(m.get("m8_turnover")),
                 _num(m.get("m9_violation_rate")), Jsonb(entry["dimension_scores"]), now, now))
    return board
