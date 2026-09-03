"""End-to-end integration: engine day-record -> his PostgreSQL schema -> read back.

Proves the trading core is database-compatible against the REAL schema (`data/database/schema.sql`,
mirrored via COMPETITION_SCHEMA_SQL). It reproduces his LIVE_WORKFLOW_EXAMPLE §6-§12 worked example
(team_alpha, 2026-09-01 -> 2026-09-02), drives `execute_decision`, persists every row through
`db_adapter`, then reconstructs NAV and performance from the stored rows and checks they match.

The `persist_day` function below is a minimal reference of his `repositories.py` / `daily_service.py`:
it resolves natural keys (team_code/ticker/date) to surrogate ids and fills the bookkeeping columns
the server owns. The engine and db_adapter never touch the database.

Run:
    COMPETITION_DATABASE_URL=postgresql://... COMPETITION_SCHEMA_SQL=/path/schema.sql \
        python competition/tests/integration_postgres.py
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(ROOT.parent / "src"))

import db_adapter
import engine

NOW = datetime(2026, 9, 2, 21, 0, tzinfo=timezone.utc)

# --- his §6/§8 worked example ------------------------------------------------------------------
TEAM_ID = 1
INSTR = {"AAPL": 1, "MSFT": 2, "NVDA": 3, "JPM": 4}     # ticker -> instrument_id (seeds names only)
AAPL, MSFT, NVDA, JPM = 1, 2, 3, 4                       # the engine runs on these ids (id-first)
ACTIVE = [AAPL, MSFT, NVDA, JPM]                         # active universe as instrument_ids
SIGNAL_DAY_ID, EXEC_DAY_ID = 1, 2
SIGNAL_DATE, EXEC_DATE = "2026-09-01", "2026-09-02"
CONSTRAINTS = {"max_asset_weight": 0.30, "max_gross_exposure": 1.00}

PRIOR = {"cash": Decimal("400000"), "shares": {AAPL: Decimal("2000"), MSFT: Decimal("800")},
         "peak_nav": Decimal("1000000")}
PRIOR_CLOSE_PX = {AAPL: Decimal("180"), MSFT: Decimal("300")}      # 09-01 close (prior valuation)
OPEN_PX = {AAPL: 182.0, MSFT: 302.0, NVDA: 120.0}                  # 09-02 open (execution)
CLOSE_PX = {AAPL: 184.0, MSFT: 305.0, NVDA: 123.0}                 # 09-02 close (valuation)
DECISION = {AAPL: 0.10, MSFT: 0.08, NVDA: 0.07}
INITIAL = Decimal("1000000")


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def seed_static(cur):
    cur.execute("INSERT INTO teams (id, team_code, display_name, api_key_hash, status, created_at, updated_at)"
                " VALUES (%s,%s,%s,%s,'ACTIVE',%s,%s)",
                (TEAM_ID, "team_alpha", "Team Alpha", "hash", NOW, NOW))
    for tk, iid in INSTR.items():
        cur.execute("INSERT INTO instruments (id, ticker, company_name, sector, is_active, created_at)"
                    " VALUES (%s,%s,%s,%s,TRUE,%s)", (iid, tk, tk, "Tech", NOW))
    for did, date in ((SIGNAL_DAY_ID, SIGNAL_DATE), (EXEC_DAY_ID, EXEC_DATE)):
        cur.execute(
            "INSERT INTO trading_days (id, trading_date, market_open_at, market_close_at,"
            " submission_open_at, submission_deadline_at, created_at, updated_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (did, date, datetime(2026, 9, int(date[-2:]), 13, 30, tzinfo=timezone.utc),
             datetime(2026, 9, int(date[-2:]), 20, 0, tzinfo=timezone.utc),
             datetime(2026, 9, int(date[-2:]), 8, 0, tzinfo=timezone.utc),
             datetime(2026, 9, int(date[-2:]), 13, 29, tzinfo=timezone.utc), NOW, NOW))
    # prior (signal-day) CLOSE portfolio = the state the engine resumes from
    pos_val = sum(PRIOR["shares"][t] * PRIOR_CLOSE_PX[t] for t in PRIOR["shares"])
    nav = PRIOR["cash"] + pos_val
    cur.execute(
        "INSERT INTO portfolio_snapshots (id, team_id, trading_day_id, snapshot_type, cash,"
        " positions_value, nav, gross_exposure, drawdown, effective_at, created_at)"
        " VALUES (1,%s,%s,'CLOSE',%s,%s,%s,%s,0,%s,%s)",
        (TEAM_ID, SIGNAL_DAY_ID, PRIOR["cash"], pos_val, nav, pos_val / nav, NOW, NOW))
    for t, q in PRIOR["shares"].items():
        mv = q * PRIOR_CLOSE_PX[t]
        cur.execute("INSERT INTO position_snapshots (portfolio_snapshot_id, instrument_id, quantity,"
                    " reference_price, market_value, weight, created_at) VALUES (1,%s,%s,%s,%s,%s,%s)",
                    (t, q, PRIOR_CLOSE_PX[t], mv, mv / nav, NOW))
    # the exact observation served + the participant's one submission (RECEIVED)
    payload = {"session_date": SIGNAL_DATE, "portfolio": {"nav": float(nav)}}
    cur.execute("INSERT INTO observations (id, team_id, trading_day_id, close_portfolio_snapshot_id,"
                " payload_json, payload_hash, generated_at, published_at, created_at)"
                " VALUES (1,%s,%s,1,%s,%s,%s,%s,%s)",
                (TEAM_ID, SIGNAL_DAY_ID, json.dumps(payload), _hash(payload), NOW, NOW, NOW))


def persist_day(cur, rec, rows):
    """Reference server persistence: resolve ids + fill bookkeeping, then INSERT in FK order."""
    ds = rows["decision_submissions"][0]
    raw = {"type": "decision_response", "target_weights": rec["decision"]["raw"]}
    cur.execute(
        "INSERT INTO decision_submissions (id, team_id, observation_id, signal_day_id, execution_day_id,"
        " idempotency_key, received_at, raw_payload_json, payload_hash, source, status, rejection_reason,"
        " validator_version, validation_policy_json, expected_weight_count, stored_weight_count,"
        " sanitized_gross_weight, weights_processed_at, agent_version, created_at, updated_at)"
        " VALUES (1,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (TEAM_ID, SIGNAL_DAY_ID, EXEC_DAY_ID, ds["idempotency_key"], NOW, json.dumps(raw),
         _hash(raw), ds["source"], ds["status"], ds["rejection_reason"], ds["validator_version"],
         json.dumps(CONSTRAINTS), ds["expected_weight_count"], ds["stored_weight_count"],
         ds["sanitized_gross_weight"], NOW, "alpha-3.1", NOW, NOW))
    for w in rows["submission_weights"]:
        cur.execute("INSERT INTO submission_weights (submission_id, instrument_id, was_provided,"
                    " raw_weight, sanitized_weight, validation_codes_json, created_at)"
                    " VALUES (1,%s,%s,%s,%s,%s,%s)",
                    (w["instrument"], w["was_provided"], w["raw_weight"], w["sanitized_weight"],
                     json.dumps(w["validation_codes_json"]), NOW))
    e = rows["executions"][0]
    cur.execute(
        "INSERT INTO executions (id, team_id, submission_id, trading_day_id, status, engine_version,"
        " scheduled_at, effective_at, processed_at, nav_before, cash_before, cash_after,"
        " total_buy_value, total_sell_value, total_fee, created_at, updated_at)"
        " VALUES (1,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (TEAM_ID, EXEC_DAY_ID, e["status"], e["engine_version"], NOW, NOW, NOW, e["nav_before"],
         e["cash_before"], e["cash_after"], e["total_buy_value"], e["total_sell_value"],
         e["total_fee"], NOW, NOW))
    for tx in rows["transactions"]:
        cur.execute("INSERT INTO transactions (execution_id, instrument_id, side, shares_before,"
                    " target_shares, quantity, price, gross_amount, fee, cash_change, status,"
                    " effective_at, processed_at, created_at) VALUES (1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (tx["instrument"], tx["side"], tx["shares_before"], tx["target_shares"],
                     tx["quantity"], tx["price"], tx["gross_amount"], tx["fee"], tx["cash_change"],
                     tx["status"], NOW, NOW, NOW))
    for cl in rows.get("cash_ledger", []):
        cur.execute("INSERT INTO cash_ledger (team_id, trading_day_id, execution_id, event_type,"
                    " amount, balance_before, balance_after, effective_at, created_at)"
                    " VALUES (%s,%s,1,%s,%s,%s,%s,%s,%s)",
                    (TEAM_ID, EXEC_DAY_ID, cl["event_type"], cl["amount"], cl["balance_before"],
                     cl["balance_after"], NOW, NOW))
    snap_id = {"POST_OPEN": 2, "CLOSE": 3}
    for snap in rows["portfolio_snapshots"]:
        st = snap["snapshot_type"]
        cur.execute("INSERT INTO portfolio_snapshots (id, team_id, trading_day_id, execution_id,"
                    " snapshot_type, cash, positions_value, nav, gross_exposure, drawdown, effective_at,"
                    " created_at) VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (snap_id[st], TEAM_ID, EXEC_DAY_ID, st, snap["cash"], snap["positions_value"],
                     snap["nav"], snap["gross_exposure"], snap["drawdown"], NOW, NOW))
    for ps in rows["position_snapshots"]:
        cur.execute("INSERT INTO position_snapshots (portfolio_snapshot_id, instrument_id, quantity,"
                    " reference_price, market_value, weight, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (snap_id[ps["snapshot_type"]], ps["instrument"], ps["quantity"],
                     ps["reference_price"], ps["market_value"], ps["weight"], NOW))
    dp = rows["daily_performance"][0]
    cur.execute("INSERT INTO daily_performance (team_id, trading_day_id, close_portfolio_snapshot_id,"
                " previous_close_nav, current_close_nav, daily_return, cumulative_return,"
                " transaction_cost, turnover, drawdown, calculated_at, created_at)"
                " VALUES (%s,%s,3,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (TEAM_ID, EXEC_DAY_ID, dp["previous_close_nav"], dp["current_close_nav"],
                 dp["daily_return"], dp["cumulative_return"], dp["transaction_cost"], dp["turnover"],
                 dp["drawdown"], NOW, NOW))


def run(dsn: str, schema_sql: str) -> None:
    import psycopg
    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(schema_sql)
            seed_static(cur)
            # --- drive the engine: the after-close batch for 2026-09-02 ---
            rec = engine.execute_decision(PRIOR, DECISION, OPEN_PX, CLOSE_PX, ACTIVE, CONSTRAINTS,
                                          prev_close_nav=INITIAL, initial_capital=INITIAL)
            assert rec["execution"]["nav_open"] == Decimal("1005600.000000000000"), \
                f'nav_open {rec["execution"]["nav_open"]} != his documented 1,005,600'
            rows = db_adapter.day_rows(rec, team_code="team_alpha", signal_date=SIGNAL_DATE,
                                       execution_date=EXEC_DATE, active_instruments=ACTIVE,
                                       idempotency_key="idem-1", received_at="2026-09-02T13:00:00Z",
                                       initial_capital=INITIAL, agent_version="alpha-3.1")
            persist_day(cur, rec, rows)
        conn.commit()

        # --- read the trajectory BACK from Postgres and reconstruct it ---
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM transactions WHERE execution_id=1")
            n_tx = cur.fetchone()[0]
            cur.execute("SELECT nav, cash FROM portfolio_snapshots WHERE team_id=1 AND trading_day_id=2"
                        " AND snapshot_type='CLOSE'")
            db_nav, db_cash = cur.fetchone()
            cur.execute("SELECT COALESCE(sum(market_value),0) FROM position_snapshots ps"
                        " JOIN portfolio_snapshots s ON s.id=ps.portfolio_snapshot_id"
                        " WHERE s.team_id=1 AND s.trading_day_id=2 AND s.snapshot_type='CLOSE'")
            db_pos = cur.fetchone()[0]
            cur.execute("SELECT current_close_nav, daily_return, transaction_cost, turnover"
                        " FROM daily_performance WHERE team_id=1 AND trading_day_id=2")
            perf_nav, perf_ret, perf_cost, perf_turn = cur.fetchone()
            cur.execute("SELECT balance_after FROM cash_ledger WHERE team_id=1 AND trading_day_id=2"
                        " ORDER BY id DESC LIMIT 1")
            ledger_end = cur.fetchone()[0]

    # reconstruction checks: DB rows reproduce the engine record exactly (12-dp Decimal)
    assert n_tx == len(rec["transactions"]) == 3, n_tx
    assert db_nav == rec["nav_close"], (db_nav, rec["nav_close"])
    assert db_cash + db_pos == db_nav, (db_cash, db_pos, db_nav)          # NAV rebuilt from positions
    assert perf_nav == rec["nav_close"]
    assert perf_cost == rec["execution"]["total_fee"]
    assert perf_turn == rec["performance"]["turnover"]
    assert ledger_end == rec["execution"]["cash_after"]                  # cash ledger reconciles
    print("PASS: engine record round-tripped through PostgreSQL and reconstructed exactly")
    print(f"  nav_open (his doc 1,005,600) : {rec['execution']['nav_open']}")
    print(f"  transactions persisted       : {n_tx}")
    print(f"  CLOSE NAV (DB == engine)     : {db_nav}")
    print(f"  NAV rebuilt from positions   : {db_cash} cash + {db_pos} positions = {db_cash + db_pos}")
    print(f"  daily_performance turnover   : {perf_turn}   cost: {perf_cost}")
    print(f"  cash_ledger end == cash_after: {ledger_end}")


def _skip_or_run():
    import pytest
    dsn = os.environ.get("COMPETITION_DATABASE_URL")
    schema = os.environ.get("COMPETITION_SCHEMA_SQL")
    if not dsn or not schema:
        pytest.skip("set COMPETITION_DATABASE_URL and COMPETITION_SCHEMA_SQL to run the DB integration")
    run(dsn, Path(schema).read_text())


def test_postgres_roundtrip():
    _skip_or_run()


if __name__ == "__main__":
    run(os.environ["COMPETITION_DATABASE_URL"], Path(os.environ["COMPETITION_SCHEMA_SQL"]).read_text())
