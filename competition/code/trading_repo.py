"""Trading persistence layer - loads and saves the trading trajectory to PostgreSQL.

This owns the *trading* side of the database - the tables tightly coupled to the accounting
logic, so their semantics stay with the execution logic that produces them:

    executions, transactions, cash_ledger, portfolio_snapshots, position_snapshots,
    daily_performance

The pure core (`engine.py`) stays DB-agnostic; THIS is the only trading module that touches the
database. The platform tables (teams, instruments, trading_days, decision_submissions,
observations, market_bars, fundamental_records, audit_logs) and all orchestration stay on the
server side. The server resolves the ids, runs the after-close batch, and invokes this repo:

    Trading Core  ->  execution record  ->  TradingRepository  ->  PostgreSQL

Daily use (server side):

    repo  = TradingRepository(conn)                       # any psycopg3 connection
    prior = repo.load_state(team_id)                      # resume from the last CLOSE
    rec   = execute_decision(prior["state"], accepted_weights, open_px, close_px,
                             active_instrument_ids, constraints,
                             prev_close_nav=prior["prev_close_nav"])
    ids   = repo.persist_day(team_id=team_id, execution_day_id=day_id,
                             submission_id=sub_id, record=rec)   # one atomic transaction

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


class TradingRepository:
    """Load/persist the trading trajectory. `conn` is a psycopg3 Connection (autocommit off)."""

    def __init__(self, conn: Any):
        self.conn = conn

    # ------------------------------------------------------------------ load
    def load_state(self, team_id: int, *,
                   initial_capital: Decimal = INITIAL_CAPITAL) -> dict[str, Any]:
        """Reconstruct the engine portfolio state from the team's latest CLOSE snapshot.

        Returns {"state": {cash, shares:{instrument_id: qty}, peak_nav}, "prev_close_nav",
        "prior_snapshot_id"}. `peak_nav` is the running max CLOSE NAV (needed for drawdown); it
        is derived from history, not stored as a column. When the team has no CLOSE snapshot yet,
        returns the initial state and prev_close_nav=None (day 1)."""
        row = self.conn.execute(
            "SELECT id, nav, cash FROM portfolio_snapshots"
            " WHERE team_id = %s AND snapshot_type = 'CLOSE'"
            " ORDER BY trading_day_id DESC LIMIT 1", (team_id,)).fetchone()
        if row is None:
            return {"state": {"cash": initial_capital, "shares": {}, "peak_nav": initial_capital},
                    "prev_close_nav": None, "prior_snapshot_id": None}
        snap_id, nav, cash = row
        positions = self.conn.execute(
            "SELECT instrument_id, quantity FROM position_snapshots WHERE portfolio_snapshot_id = %s",
            (snap_id,)).fetchall()
        shares = {int(iid): qty for iid, qty in positions}
        peak = self.conn.execute(
            "SELECT max(nav) FROM portfolio_snapshots WHERE team_id = %s AND snapshot_type = 'CLOSE'",
            (team_id,)).fetchone()[0] or nav
        return {"state": {"cash": cash, "shares": shares, "peak_nav": peak},
                "prev_close_nav": nav, "prior_snapshot_id": snap_id}

    # --------------------------------------------------------------- persist
    def persist_day(self, *, team_id: int, execution_day_id: int, submission_id: int,
                    record: dict[str, Any], initial_capital: Decimal = INITIAL_CAPITAL,
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

        `submission_id` is the platform-owned decision the execution belongs to (the server
        creates a FALLBACK submission for a held/no-show day).

        Audit timestamps - all optional, each defaults to `now`, so existing callers are
        unchanged. The batch runs once after the close, but the times mean different things:
          open_effective_at  - when the fill economically occurred (the official open); used for
                               the execution, transactions, cash_ledger, and the POST_OPEN snapshot
          close_effective_at - the official close; used for the CLOSE snapshot
          scheduled_at       - executions.scheduled_at (planned open); defaults to open_effective_at
          processed_at       - when the server actually ran the batch; used for processed_at /
                               calculated_at / created_at / updated_at

        Returns the created ids."""
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
            if ledger and ledger[-1]["balance_after"] != e["cash_after"]:
                raise ValueError(
                    f"cash_ledger end {ledger[-1]['balance_after']} != cash_after {e['cash_after']}")

            snap_ids: dict[str, int] = {}
            for kind, snap, drawdown, eff in (("POST_OPEN", o, Decimal(0), open_at),
                                              ("CLOSE", c, c["drawdown"], close_at)):
                sid = conn.execute(
                    "INSERT INTO portfolio_snapshots (team_id, trading_day_id, execution_id,"
                    " snapshot_type, cash, positions_value, nav, gross_exposure, drawdown,"
                    " effective_at, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (team_id, execution_day_id, exec_id, kind, snap["cash"], snap["positions_value"],
                     snap["nav"], snap["gross_exposure"], drawdown, eff, proc_at)).fetchone()[0]
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
