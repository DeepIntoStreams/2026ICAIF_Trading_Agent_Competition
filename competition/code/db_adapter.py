"""Reference mapping: one enriched engine day-record -> rows for the live competition database.

This is the seam between the trading core and the deployment DB (his data/database/schema.sql).
The engine stays database-free: it RETURNS a complete day record; this module turns that record
into per-table rows the server can INSERT. Pure functions, no DB driver.

Usage on the server (once per day, after the close):

    rec = execute_decision(state, target_weights, open_px, close_px, instruments, constraints,
                           prev_close_nav=prev_nav)          # engine
    rows = day_rows(rec, team_code="team_a", signal_date="2026-06-04",
                    execution_date="2026-06-05", active_instruments=UNIVERSE,
                    idempotency_key=key, received_at=ts)     # this module
    # rows["transactions"], rows["portfolio_snapshots"], ... -> INSERT into the matching table
    db.save_state(team, rec["new_state"])                    # resume here next day

Each row carries the DOMAIN columns that come from the engine, plus NATURAL KEYS
(`team_code`, the two dates, `instrument`). The `instrument` key is id-first: whatever key the
engine was driven with - an `instrument_id` int (preferred, stable across ticker renames) or a
ticker string. The server maps the natural keys to surrogate ids (team_code -> teams.id,
date -> trading_days.id, instrument -> instruments.id, or straight through when it is already an
id) and adds the bookkeeping columns it owns (created_at/updated_at, validator_version, foreign-key
ids, portfolio_snapshot_id for the position rows). Column names below match schema.sql exactly.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def _dec(x: Any) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


ENGINE_VERSION = "engine-1.1"
VALIDATOR_VERSION = "reject-not-repair-1.0"

# Which columns each helper emits (schema.sql columns + the natural keys the server resolves).
# The test asserts every emitted row conforms to this, so drift is caught early.
NATURAL_KEYS = {"team_code", "signal_date", "execution_date", "trading_date", "instrument"}
SCHEMA_COLUMNS: dict[str, set[str]] = {
    "decision_submissions": {"idempotency_key", "received_at", "status", "rejection_reason",
                             "source", "agent_version", "validator_version",
                             "expected_weight_count", "stored_weight_count",
                             "sanitized_gross_weight"} | NATURAL_KEYS,
    "submission_weights": {"was_provided", "raw_weight", "sanitized_weight",
                           "validation_codes_json"} | NATURAL_KEYS,
    "executions": {"status", "engine_version", "nav_before", "cash_before", "cash_after",
                   "total_buy_value", "total_sell_value", "total_fee"} | NATURAL_KEYS,
    "transactions": {"side", "shares_before", "target_shares", "quantity", "price",
                     "gross_amount", "fee", "cash_change", "status"} | NATURAL_KEYS,
    "portfolio_snapshots": {"snapshot_type", "cash", "positions_value", "nav",
                            "gross_exposure", "drawdown"} | NATURAL_KEYS,
    "position_snapshots": {"snapshot_type", "quantity", "reference_price", "market_value",
                           "weight"} | NATURAL_KEYS,
    "daily_performance": {"previous_close_nav", "current_close_nav", "daily_return",
                          "cumulative_return", "transaction_cost", "turnover",
                          "drawdown"} | NATURAL_KEYS,
    "cash_ledger": {"event_type", "amount", "balance_before", "balance_after"} | NATURAL_KEYS,
}


def decision_submission_row(rec: dict[str, Any], *, team_code: str, signal_date: str,
                            execution_date: str, idempotency_key: str, received_at: str,
                            active_instruments: list[Any], agent_version: str | None = None) -> dict[str, Any]:
    """-> decision_submissions. status is RECEIVED (no submission), REJECTED (invalid), or
    EXECUTED (valid, filled). A rejected/absent decision leaves stored_weight_count = 0, which
    the schema's `status IN (RECEIVED, REJECTED) OR (weights complete)` CHECK permits."""
    dec = rec["decision"]
    if not dec["submitted"]:
        status, reason, source = "RECEIVED", "no_submission", "FALLBACK"
    elif dec["violations"]:
        status, reason, source = "REJECTED", ";".join(map(str, dec["violations"])), "PARTICIPANT"
    else:
        status, reason, source = "EXECUTED", None, "PARTICIPANT"
    accepted = dec["accepted"]
    stored = len(active_instruments) if dec["executed"] else 0
    gross = sum(abs(w) for w in accepted.values()) if dec["executed"] else None
    return {"team_code": team_code, "signal_date": signal_date, "execution_date": execution_date,
            "idempotency_key": idempotency_key, "received_at": received_at,
            "status": status, "rejection_reason": reason, "source": source,
            "agent_version": agent_version, "validator_version": VALIDATOR_VERSION,
            "expected_weight_count": len(active_instruments), "stored_weight_count": stored,
            "sanitized_gross_weight": gross}


def submission_weight_rows(rec: dict[str, Any], *, team_code: str, signal_date: str,
                           execution_date: str, active_instruments: list[Any]) -> list[dict[str, Any]]:
    """-> submission_weights, one row per active instrument, ONLY for an executed (accepted)
    decision. Under reject-not-repair the accepted weight IS the raw weight (or 0 if omitted);
    a rejected/absent decision stores no weight rows."""
    dec = rec["decision"]
    if not dec["executed"]:
        return []
    raw, accepted = dec["raw"], dec["accepted"]
    rows = []
    for inst in active_instruments:
        provided = inst in raw
        rows.append({"team_code": team_code, "signal_date": signal_date,
                     "execution_date": execution_date, "instrument": inst,
                     "was_provided": provided, "raw_weight": (raw.get(inst) if provided else None),
                     "sanitized_weight": float(accepted.get(inst, 0.0)), "validation_codes_json": []})
    return rows


def execution_row(rec: dict[str, Any], *, team_code: str, execution_date: str) -> dict[str, Any] | None:
    """-> executions (one per team-day), ONLY when a fill happened. nav_before is the open NAV."""
    if not rec["executed"]:
        return None
    e = rec["execution"]
    return {"team_code": team_code, "execution_date": execution_date, "status": "COMPLETED",
            "engine_version": ENGINE_VERSION, "nav_before": e["nav_open"],
            "cash_before": e["cash_before"], "cash_after": e["cash_after"],
            "total_buy_value": e["total_buy_value"], "total_sell_value": e["total_sell_value"],
            "total_fee": e["total_fee"]}


def transaction_rows(rec: dict[str, Any], *, team_code: str, execution_date: str) -> list[dict[str, Any]]:
    """-> transactions, one row per instrument that actually traded."""
    return [{"team_code": team_code, "execution_date": execution_date, **tx, "status": "COMPLETED"}
            for tx in rec["transactions"]]


def portfolio_snapshot_rows(rec: dict[str, Any], *, team_code: str,
                            execution_date: str) -> list[dict[str, Any]]:
    """-> portfolio_snapshots: the POST_OPEN snapshot (after the fill) and the CLOSE snapshot."""
    o, c = rec["open_snapshot"], rec["close_snapshot"]
    return [
        {"team_code": team_code, "trading_date": execution_date, "snapshot_type": "POST_OPEN",
         "cash": o["cash"], "positions_value": o["positions_value"], "nav": o["nav"],
         "gross_exposure": o["gross_exposure"], "drawdown": 0.0},
        {"team_code": team_code, "trading_date": execution_date, "snapshot_type": "CLOSE",
         "cash": c["cash"], "positions_value": c["positions_value"], "nav": c["nav"],
         "gross_exposure": c["gross_exposure"], "drawdown": c["drawdown"]},
    ]


def position_snapshot_rows(rec: dict[str, Any], *, team_code: str,
                           execution_date: str) -> list[dict[str, Any]]:
    """-> position_snapshots for both snapshots. Each row is tagged with its snapshot_type so the
    server can attach it to the matching portfolio_snapshot_id after inserting the snapshots."""
    rows = []
    for snap_type, snap in (("POST_OPEN", rec["open_snapshot"]), ("CLOSE", rec["close_snapshot"])):
        for p in snap["positions"]:
            rows.append({"team_code": team_code, "trading_date": execution_date,
                         "snapshot_type": snap_type, "instrument": p["instrument"],
                         "quantity": p["quantity"], "reference_price": p["reference_price"],
                         "market_value": p["market_value"], "weight": p["weight"]})
    return rows


def cash_ledger_rows(rec: dict[str, Any], *, team_code: str,
                     execution_date: str) -> list[dict[str, Any]]:
    """-> cash_ledger: an ordered, running-balance trail of every cash movement on the day. Each
    trade contributes a TRADE leg (gross, signed) and a FEE leg, so the balance chains from
    `execution.cash_before` to `execution.cash_after` and proves cash continuity. Empty on a
    held/rejected day (no trades). The server attaches execution_id / transaction_id."""
    if not rec["executed"]:
        return []
    running = _dec(rec["execution"]["cash_before"])
    rows: list[dict[str, Any]] = []
    for tx in rec["transactions"]:
        gross, fee = _dec(tx["gross_amount"]), _dec(tx["fee"])
        amount = gross if tx["side"] == "SELL" else -gross              # TRADE leg (pre-fee)
        before, running = running, running + amount
        rows.append({"team_code": team_code, "trading_date": execution_date, "instrument": tx["instrument"],
                     "event_type": "TRADE", "amount": amount,
                     "balance_before": before, "balance_after": running})
        if fee != 0:
            before, running = running, running - fee
            rows.append({"team_code": team_code, "trading_date": execution_date, "instrument": tx["instrument"],
                         "event_type": "FEE", "amount": -fee,
                         "balance_before": before, "balance_after": running})
    return rows


def daily_performance_row(rec: dict[str, Any], *, team_code: str, execution_date: str,
                          initial_capital: float) -> dict[str, Any]:
    """-> daily_performance. previous_close_nav is NOT NULL / >= 0 in the schema, so on day 1
    (prev is None) it falls back to the initial capital."""
    p = rec["performance"]
    prev = p["previous_close_nav"] if p["previous_close_nav"] is not None else _dec(initial_capital)
    daily_ret = p["daily_return"] if p["daily_return"] is not None else (
        _dec(rec["nav_close"]) / prev - 1 if prev else Decimal(0))
    return {"team_code": team_code, "trading_date": execution_date,
            "previous_close_nav": prev, "current_close_nav": p["current_close_nav"],
            "daily_return": daily_ret, "cumulative_return": p["cumulative_return"],
            "transaction_cost": p["transaction_cost"], "turnover": p["turnover"],
            "drawdown": p["drawdown"]}


def day_rows(rec: dict[str, Any], *, team_code: str, signal_date: str, execution_date: str,
             active_instruments: list[Any], idempotency_key: str, received_at: str,
             initial_capital: float = 1_000_000.0,
             agent_version: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """Assemble every table's rows for one team-day. Insert in this order (parents first):
    decision_submissions -> submission_weights -> executions -> transactions -> cash_ledger ->
    portfolio_snapshots -> position_snapshots -> daily_performance."""
    rows: dict[str, list[dict[str, Any]]] = {
        "decision_submissions": [decision_submission_row(
            rec, team_code=team_code, signal_date=signal_date, execution_date=execution_date,
            idempotency_key=idempotency_key, received_at=received_at,
            active_instruments=active_instruments, agent_version=agent_version)],
        "submission_weights": submission_weight_rows(
            rec, team_code=team_code, signal_date=signal_date, execution_date=execution_date,
            active_instruments=active_instruments),
        "portfolio_snapshots": portfolio_snapshot_rows(
            rec, team_code=team_code, execution_date=execution_date),
        "position_snapshots": position_snapshot_rows(
            rec, team_code=team_code, execution_date=execution_date),
        "daily_performance": [daily_performance_row(
            rec, team_code=team_code, execution_date=execution_date, initial_capital=initial_capital)],
    }
    exec_row = execution_row(rec, team_code=team_code, execution_date=execution_date)
    if exec_row is not None:
        rows["executions"] = [exec_row]
        rows["transactions"] = transaction_rows(rec, team_code=team_code, execution_date=execution_date)
        rows["cash_ledger"] = cash_ledger_rows(rec, team_code=team_code, execution_date=execution_date)
    return rows
