"""End-to-end integration: TradingRepository against his real PostgreSQL schema.

Proves the trading persistence layer (competition/code/trading_repo.py) works against the REAL
schema (`data/database/schema.sql`, mirrored via COMPETITION_SCHEMA_SQL). It reproduces his
LIVE_WORKFLOW_EXAMPLE §6-§12 (team_alpha, 2026-09-01 -> 2026-09-02):

  1. seed the PLATFORM tables the server owns (teams, instruments, trading_days, the prior CLOSE
     portfolio, the observation, the received+validated submission);
  2. `repo.load_state(team)`   -> reconstruct the engine state from the DB;
  3. `execute_decision(...)`   -> the after-close batch (id-first, Decimal);
  4. `repo.persist_day(...)`   -> persist the whole trajectory atomically;
  5. read it back and reconstruct NAV / performance / cash-continuity from the stored rows.

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

import engine
from trading_repo import TradingRepository

NOW = datetime(2026, 9, 2, 21, 0, tzinfo=timezone.utc)

# --- his §6/§8 worked example (id-first: the engine runs on instrument_ids) --------------------
TEAM_ID = 1
INSTR = {"AAPL": 1, "MSFT": 2, "NVDA": 3, "JPM": 4}     # ticker -> instrument_id (seeds names only)
AAPL, MSFT, NVDA, JPM = 1, 2, 3, 4
ACTIVE = [AAPL, MSFT, NVDA, JPM]
SIGNAL_DAY_ID, EXEC_DAY_ID, SUBMISSION_ID = 1, 2, 1
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


def seed_platform(conn):
    """Everything the SERVER owns: teams, instruments, calendar, prior CLOSE portfolio, the served
    observation, and the received+validated submission. (Trading tables are the repo's job.)"""
    conn.execute("INSERT INTO teams (id, team_code, display_name, api_key_hash, status, created_at,"
                 " updated_at) VALUES (%s,%s,%s,%s,'ACTIVE',%s,%s)",
                 (TEAM_ID, "team_alpha", "Team Alpha", "hash", NOW, NOW))
    for tk, iid in INSTR.items():
        conn.execute("INSERT INTO instruments (id, ticker, company_name, sector, is_active,"
                     " created_at) VALUES (%s,%s,%s,%s,TRUE,%s)", (iid, tk, tk, "Tech", NOW))
    for did, date in ((SIGNAL_DAY_ID, SIGNAL_DATE), (EXEC_DAY_ID, EXEC_DATE)):
        d = int(date[-2:])
        conn.execute(
            "INSERT INTO trading_days (id, trading_date, market_open_at, market_close_at,"
            " submission_open_at, submission_deadline_at, created_at, updated_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (did, date, datetime(2026, 9, d, 13, 30, tzinfo=timezone.utc),
             datetime(2026, 9, d, 20, 0, tzinfo=timezone.utc),
             datetime(2026, 9, d, 8, 0, tzinfo=timezone.utc),
             datetime(2026, 9, d, 13, 29, tzinfo=timezone.utc), NOW, NOW))
    # prior (signal-day) CLOSE portfolio - the state the repo will reconstruct
    pos_val = sum(PRIOR["shares"][t] * PRIOR_CLOSE_PX[t] for t in PRIOR["shares"])
    nav = PRIOR["cash"] + pos_val
    prior_snap_id = conn.execute(
        "INSERT INTO portfolio_snapshots (team_id, trading_day_id, snapshot_type, cash,"
        " positions_value, nav, gross_exposure, drawdown, effective_at, created_at)"
        " VALUES (%s,%s,'CLOSE',%s,%s,%s,%s,0,%s,%s) RETURNING id",
        (TEAM_ID, SIGNAL_DAY_ID, PRIOR["cash"], pos_val, nav, pos_val / nav, NOW, NOW)).fetchone()[0]
    for iid, q in PRIOR["shares"].items():
        mv = q * PRIOR_CLOSE_PX[iid]
        conn.execute("INSERT INTO position_snapshots (portfolio_snapshot_id, instrument_id,"
                     " quantity, reference_price, market_value, weight, created_at)"
                     " VALUES (%s,%s,%s,%s,%s,%s,%s)",
                     (prior_snap_id, iid, q, PRIOR_CLOSE_PX[iid], mv, mv / nav, NOW))
    payload = {"session_date": SIGNAL_DATE, "portfolio": {"nav": float(nav)}}
    conn.execute("INSERT INTO observations (id, team_id, trading_day_id, close_portfolio_snapshot_id,"
                 " payload_json, payload_hash, generated_at, published_at, created_at)"
                 " VALUES (1,%s,%s,%s,%s,%s,%s,%s,%s)",
                 (TEAM_ID, SIGNAL_DAY_ID, prior_snap_id, json.dumps(payload), _hash(payload),
                  NOW, NOW, NOW))
    raw = {"type": "decision_response", "target_weights": {str(k): v for k, v in DECISION.items()}}
    conn.execute(
        "INSERT INTO decision_submissions (id, team_id, observation_id, signal_day_id,"
        " execution_day_id, idempotency_key, received_at, raw_payload_json, payload_hash, source,"
        " status, validator_version, validation_policy_json, expected_weight_count,"
        " stored_weight_count, sanitized_gross_weight, weights_processed_at, agent_version,"
        " created_at, updated_at)"
        " VALUES (%s,%s,1,%s,%s,%s,%s,%s,%s,'PARTICIPANT','QUEUED',%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (SUBMISSION_ID, TEAM_ID, SIGNAL_DAY_ID, EXEC_DAY_ID, "idem-1", NOW, json.dumps(raw),
         _hash(raw), "reject-not-repair-1.0", json.dumps(CONSTRAINTS), len(ACTIVE), len(ACTIVE),
         Decimal("0.25"), NOW, "alpha-3.1", NOW, NOW))


def run(dsn: str, schema_sql: str) -> None:
    import psycopg
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(schema_sql)
        seed_platform(conn)

        repo = TradingRepository(conn)
        # 2) reconstruct the engine state from the DB (the last CLOSE)
        prior = repo.load_state(TEAM_ID, initial_capital=INITIAL)
        assert prior["state"]["cash"] == Decimal("400000"), prior["state"]["cash"]
        assert prior["state"]["shares"] == {AAPL: Decimal("2000"), MSFT: Decimal("800")}, prior["state"]["shares"]
        assert prior["prev_close_nav"] == Decimal("1000000")

        # 3) the after-close batch, id-first
        rec = engine.execute_decision(prior["state"], DECISION, OPEN_PX, CLOSE_PX, ACTIVE,
                                      CONSTRAINTS, prev_close_nav=prior["prev_close_nav"],
                                      initial_capital=INITIAL)
        assert rec["execution"]["nav_open"] == Decimal("1005600.000000000000"), rec["execution"]["nav_open"]

        # 4) persist the whole trajectory atomically
        ids = repo.persist_day(team_id=TEAM_ID, execution_day_id=EXEC_DAY_ID,
                               submission_id=SUBMISSION_ID, record=rec, initial_capital=INITIAL)

        # 5) read the trajectory BACK and reconstruct it
        n_tx = conn.execute("SELECT count(*) FROM transactions WHERE execution_id = %s",
                            (ids["execution_id"],)).fetchone()[0]
        db_nav, db_cash = conn.execute(
            "SELECT nav, cash FROM portfolio_snapshots WHERE id = %s",
            (ids["close_snapshot_id"],)).fetchone()
        db_pos = conn.execute(
            "SELECT COALESCE(sum(market_value), 0) FROM position_snapshots WHERE portfolio_snapshot_id = %s",
            (ids["close_snapshot_id"],)).fetchone()[0]
        perf_nav, perf_cost, perf_turn = conn.execute(
            "SELECT current_close_nav, transaction_cost, turnover FROM daily_performance"
            " WHERE team_id = %s AND trading_day_id = %s", (TEAM_ID, EXEC_DAY_ID)).fetchone()
        ledger_end = conn.execute(
            "SELECT balance_after FROM cash_ledger WHERE execution_id = %s ORDER BY id DESC LIMIT 1",
            (ids["execution_id"],)).fetchone()[0]
        # the repo can also reconstruct the state for the NEXT day
        nxt = repo.load_state(TEAM_ID, initial_capital=INITIAL)

    assert n_tx == len(rec["transactions"]) == 3, n_tx
    assert db_nav == rec["nav_close"], (db_nav, rec["nav_close"])
    assert db_cash + db_pos == db_nav, (db_cash, db_pos, db_nav)
    assert perf_nav == rec["nav_close"]
    assert perf_cost == rec["execution"]["total_fee"]
    assert perf_turn == rec["performance"]["turnover"]
    assert ledger_end == rec["execution"]["cash_after"]
    assert nxt["prev_close_nav"] == rec["nav_close"]             # next day resumes from this close
    assert nxt["state"]["cash"] == rec["new_state"]["cash"]
    print("PASS: TradingRepository load -> execute -> persist -> reconstruct, on the real schema")
    print(f"  load_state -> nav_open (his doc 1,005,600) : {rec['execution']['nav_open']}")
    print(f"  transactions persisted                     : {n_tx}")
    print(f"  CLOSE NAV (DB == engine)                   : {db_nav}")
    print(f"  NAV rebuilt from positions                 : {db_cash} + {db_pos} = {db_cash + db_pos}")
    print(f"  daily_performance turnover / cost          : {perf_turn} / {perf_cost}")
    print(f"  cash_ledger end == cash_after              : {ledger_end}")
    print(f"  next-day load_state cash / prev_nav        : {nxt['state']['cash']} / {nxt['prev_close_nav']}")


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
